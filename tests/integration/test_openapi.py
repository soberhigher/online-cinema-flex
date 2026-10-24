from app.main import app


def test_auth_routes_are_registered_in_openapi() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/auth/register",
        "/api/v1/auth/activate",
        "/api/v1/auth/resend-activation",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/change-password",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
    }
    assert expected <= set(paths)


def test_openapi_documents_business_errors_and_catalog_parameters() -> None:
    paths = app.openapi()["paths"]
    expected_statuses = {
        ("/api/v1/auth/register", "post"): {"409", "422"},
        ("/api/v1/auth/change-password", "post"): {"400", "401", "422"},
        ("/api/v1/catalog-admin/movies", "post"): {"401", "403", "404", "409", "422"},
        ("/api/v1/favorites/{movie_id}", "post"): {"401", "404", "409", "422"},
        ("/api/v1/orders", "post"): {"401", "409"},
        ("/api/v1/payments/webhook", "post"): {"400", "409", "422"},
    }
    for (path, method), expected in expected_statuses.items():
        assert expected <= set(paths[path][method]["responses"])
    catalog = paths["/api/v1/movies/"]["get"]
    assert {"page", "per_page", "search", "genre_id", "year", "min_imdb", "sort"} <= {
        parameter["name"] for parameter in catalog["parameters"]
    }
    assert {"422"} <= set(paths["/api/v1/movies/genres"]["get"]["responses"])
    schema = catalog["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("MoviePage")
