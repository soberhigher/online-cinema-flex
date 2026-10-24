"""Management commands.

python -m app.cli create-user --email admin@example.com --password "..." --role ADMIN
python -m app.cli seed-catalog
"""

import argparse
import asyncio
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.security import hash_password, validate_password
from app.models.accounts import User, UserGroup, UserGroupEnum
from app.models.catalog import Certification, Director, Genre, Movie, Star

MOVIES: list[dict[str, Any]] = [
    {"name": "The Shawshank Redemption", "year": 1994, "time": 142, "imdb": "9.3",
     "votes": 2900000, "price": "9.99", "cert": "R", "genres": ["Drama"],
     "stars": ["Tim Robbins", "Morgan Freeman"], "directors": ["Frank Darabont"],
     "description": "Two imprisoned men bond over a number of years."},
    {"name": "The Dark Knight", "year": 2008, "time": 152, "imdb": "9.0", "votes": 2800000,
     "price": "11.99", "cert": "PG-13", "genres": ["Action", "Crime", "Drama"],
     "stars": ["Christian Bale", "Heath Ledger"], "directors": ["Christopher Nolan"],
     "description": "Batman faces the Joker, a criminal mastermind who wants chaos."},
    {"name": "Inception", "year": 2010, "time": 148, "imdb": "8.8", "votes": 2500000,
     "price": "10.99", "cert": "PG-13", "genres": ["Action", "Sci-Fi"],
     "stars": ["Leonardo DiCaprio"], "directors": ["Christopher Nolan"],
     "description": "A thief who steals corporate secrets through dream-sharing technology."},
    {"name": "Pulp Fiction", "year": 1994, "time": 154, "imdb": "8.9", "votes": 2200000,
     "price": "8.99", "cert": "R", "genres": ["Crime", "Drama"],
     "stars": ["John Travolta", "Uma Thurman"], "directors": ["Quentin Tarantino"],
     "description": "The lives of two mob hitmen, a boxer and others intertwine."},
    {"name": "Toy Story", "year": 1995, "time": 81, "imdb": "8.3", "votes": 1100000,
     "price": "6.99", "cert": "G", "genres": ["Animation", "Comedy"],
     "stars": ["Tom Hanks", "Tim Allen"], "directors": ["John Lasseter"],
     "description": "A cowboy doll is threatened by a new spaceman action figure."},
    {"name": "Interstellar", "year": 2014, "time": 169, "imdb": "8.7", "votes": 2200000,
     "price": "12.99", "cert": "PG-13", "genres": ["Drama", "Sci-Fi"],
     "stars": ["Matthew McConaughey", "Anne Hathaway"], "directors": ["Christopher Nolan"],
     "description": "Explorers travel through a wormhole to save humanity."},
    {"name": "Back to the Future", "year": 1985, "time": 116, "imdb": "8.5", "votes": 1300000,
     "price": "7.99", "cert": "PG", "genres": ["Comedy", "Sci-Fi"],
     "stars": ["Michael J. Fox"], "directors": ["Robert Zemeckis"],
     "description": "A teenager is accidentally sent thirty years into the past."},
]  # fmt: skip


async def create_user(session: AsyncSession, email: str, password: str, role: str) -> str:
    validate_password(password)
    email = email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise SystemExit(f"User {email} already exists")
    group = await session.scalar(select(UserGroup).where(UserGroup.name == UserGroupEnum(role)))
    if group is None:
        raise SystemExit("User groups are missing; run `alembic upgrade head` first")
    session.add(
        User(email=email, password_hash=hash_password(password), group_id=group.id, is_active=True)
    )
    await session.commit()
    return f"Created active {role} user {email}"


async def _named(session: AsyncSession, model: type[Any], name: str) -> Any:
    row = await session.scalar(select(model).where(model.name == name))
    if row is None:
        row = model(name=name)
        session.add(row)
        await session.flush()
    return row


async def seed_catalog(session: AsyncSession) -> str:
    created = 0
    for data in MOVIES:
        exists = await session.scalar(
            select(Movie.id).where(
                Movie.name == data["name"], Movie.year == data["year"], Movie.time == data["time"]
            )
        )
        if exists:
            continue
        session.add(
            Movie(
                name=data["name"],
                year=data["year"],
                time=data["time"],
                imdb=Decimal(data["imdb"]),
                votes=data["votes"],
                price=Decimal(data["price"]),
                description=data["description"],
                certification=await _named(session, Certification, data["cert"]),
                genres=[await _named(session, Genre, name) for name in data["genres"]],
                stars=[await _named(session, Star, name) for name in data["stars"]],
                directors=[await _named(session, Director, name) for name in data["directors"]],
            )
        )
        created += 1
    await session.commit()
    return f"Seeded {created} movies ({len(MOVIES) - created} already present)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    user = commands.add_parser("create-user", help="Create an active user")
    user.add_argument("--email", required=True)
    user.add_argument("--password", required=True)
    user.add_argument("--role", choices=[group.value for group in UserGroupEnum], default="USER")
    commands.add_parser("seed-catalog", help="Insert demo genres, people and movies")
    return parser


async def run(args: argparse.Namespace) -> str:
    async with async_session_factory() as session:
        if args.command == "create-user":
            return await create_user(session, args.email, args.password, args.role)
        return await seed_catalog(session)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        print(asyncio.run(run(args)))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
