from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.catalog import Movie
from app.models.purchases import Cart, CartItem, Order, OrderItem, OrderStatus
from app.services.auth import AuthError


class PurchaseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _cart(
        self, user_id: UUID, *, create: bool = False, lock: bool = False
    ) -> Cart | None:
        statement = (
            select(Cart)
            .options(
                selectinload(Cart.items).selectinload(CartItem.movie).selectinload(Movie.genres)
            )
            .where(Cart.user_id == user_id)
        )
        if lock:
            statement = statement.with_for_update()
        cart = await self.session.scalar(statement)
        if cart is None and create:
            cart = Cart(user_id=user_id, items=[])
            self.session.add(cart)
            await self.session.flush()
            return cart
        return cart

    async def cart(self, user_id: UUID) -> dict[str, object]:
        cart = await self._cart(user_id)
        items = [] if cart is None else cart.items
        total = sum((item.movie.price for item in items), Decimal("0.00"))
        return {
            "items": [
                {
                    "movie_id": item.movie.id,
                    "title": item.movie.name,
                    "price": item.movie.price,
                    "year": item.movie.year,
                    "genres": [genre.name for genre in item.movie.genres],
                }
                for item in items
            ],
            "total": total,
        }

    async def add_to_cart(self, user_id: UUID, movie_id: UUID) -> dict[str, str]:
        movie = await self.session.get(Movie, movie_id)
        if movie is None:
            raise AuthError(404, "Movie not found")
        if await self._owned_or_pending(user_id, movie_id):
            raise AuthError(409, "Movie is already purchased or pending payment")
        cart = await self._cart(user_id, create=True, lock=True)
        assert cart is not None
        if any(item.movie_id == movie_id for item in cart.items):
            raise AuthError(409, "Movie is already in the cart")
        cart.items.append(CartItem(movie_id=movie_id, movie=movie))
        await self.session.commit()
        return {"message": "Added to cart"}

    async def remove_from_cart(self, user_id: UUID, movie_id: UUID) -> None:
        cart = await self._cart(user_id, lock=True)
        item = (
            None
            if cart is None
            else next((entry for entry in cart.items if entry.movie_id == movie_id), None)
        )
        if item is None:
            raise AuthError(404, "Movie is not in the cart")
        await self.session.delete(item)
        await self.session.commit()

    async def clear_cart(self, user_id: UUID) -> None:
        cart = await self._cart(user_id, lock=True)
        if cart is not None:
            for item in list(cart.items):
                await self.session.delete(item)
            await self.session.commit()

    async def create_order(self, user_id: UUID) -> dict[str, object]:
        cart = await self._cart(user_id, lock=True)
        if cart is None or not cart.items:
            raise AuthError(409, "Cart is empty")
        eligible: list[CartItem] = []
        excluded: list[dict[str, str]] = []
        for item in cart.items:
            if await self._owned_or_pending(user_id, item.movie_id):
                excluded.append({"movie_id": str(item.movie_id), "reason": "purchased_or_pending"})
            else:
                eligible.append(item)
        if not eligible:
            raise AuthError(409, {"excluded": excluded})
        total = sum((item.movie.price for item in eligible), Decimal("0.00"))
        order = Order(user_id=user_id, status=OrderStatus.PENDING, total_amount=total)
        order.items = [
            OrderItem(movie_id=item.movie_id, price_at_order=item.movie.price) for item in eligible
        ]
        self.session.add(order)
        for item in eligible:
            await self.session.delete(item)
        await self.session.commit()
        return {"order": await self.order(user_id, order.id), "excluded": excluded}

    async def _owned_or_pending(self, user_id: UUID, movie_id: UUID) -> bool:
        paid = await self.session.scalar(
            select(OrderItem.id)
            .join(Order)
            .where(
                Order.user_id == user_id,
                OrderItem.movie_id == movie_id,
                Order.status == OrderStatus.PAID,
            )
        )
        pending = await self.session.scalar(
            select(OrderItem.id)
            .join(Order)
            .where(
                Order.user_id == user_id,
                OrderItem.movie_id == movie_id,
                Order.status == OrderStatus.PENDING,
            )
        )
        return paid is not None or pending is not None

    async def order(self, user_id: UUID, order_id: UUID) -> dict[str, object]:
        order = await self.session.scalar(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.movie))
            .where(Order.id == order_id, Order.user_id == user_id)
        )
        if order is None:
            raise AuthError(404, "Order not found")
        return self._order_data(order)

    @staticmethod
    def _order_data(order: Order) -> dict[str, object]:
        return {
            "id": order.id,
            "status": order.status,
            "total_amount": order.total_amount,
            "created_at": order.created_at,
            "items": [
                {
                    "movie_id": item.movie_id,
                    "title": item.movie.name,
                    "price_at_order": item.price_at_order,
                }
                for item in order.items
            ],
        }

    async def orders(self, user_id: UUID) -> list[dict[str, object]]:
        rows = await self.session.scalars(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.movie))
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
        )
        return [self._order_data(order) for order in rows.all()]

    async def cancel_order(self, user_id: UUID, order_id: UUID) -> dict[str, object]:
        order = await self.session.scalar(
            select(Order).where(Order.id == order_id, Order.user_id == user_id).with_for_update()
        )
        if order is None:
            raise AuthError(404, "Order not found")
        if order.status != OrderStatus.PENDING:
            raise AuthError(409, "Only pending orders can be canceled")
        order.status = OrderStatus.CANCELED
        await self.session.commit()
        return {"id": order.id, "status": order.status}
