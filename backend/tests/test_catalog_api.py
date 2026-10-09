"""The search endpoint, end to end through DRF and Postgres."""

from __future__ import annotations

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.catalog.infrastructure.models import Product
from config import container
from tests.conftest import DEMO_PASSWORD

SEARCH = "/api/v1/catalog/products/"


@pytest.fixture
def catalog(db) -> None:
    Product.objects.bulk_create(
        [
            Product(
                sku="BM-1001",
                name="Бумага офисная А4, 500 листов",
                description="Плотность 80 г/м² для принтеров.",
                category="Бумага и бланки",
                price=Decimal("389.00"),
                unit="пачка",
            ),
            Product(
                sku="TH-5001",
                name="Ноутбук 15.6 дюймов",
                description="Матовый экран Full HD.",
                category="Техника",
                price=Decimal("78900.00"),
                unit="шт",
            ),
            Product(
                sku="KT-2001",
                name="Картридж лазерный чёрный",
                description="Ресурс 3000 страниц.",
                category="Расходные материалы",
                price=Decimal("4150.00"),
                unit="шт",
            ),
        ]
    )
    # Derived columns: without this the rows exist but match nothing.
    container.product_repository().refresh_search_vectors()
    # The vocabulary and both spelling indexes are cached per process; a previous test's
    # catalogue would otherwise still be what corrections aim at.
    container.reset_spelling_indexes()


@pytest.fixture
def client(account) -> APIClient:
    api = APIClient()
    api.post(
        "/api/v1/auth/login/",
        {"email": account.email, "password": DEMO_PASSWORD},
        format="json",
    )
    return api


def test_search_requires_a_session(db) -> None:
    assert APIClient().get(SEARCH, {"q": "бумага"}).status_code == 403


def test_an_empty_query_lists_the_catalogue(client, catalog) -> None:
    response = client.get(SEARCH)

    assert response.status_code == 200
    assert response.data["total"] == 3
    assert response.data["correction"]["changed"] is False


def test_search_matches_by_stem(client, catalog) -> None:
    # "ноутбука" is the genitive; the russian text-search configuration has to stem it.
    response = client.get(SEARCH, {"q": "ноутбука"})

    assert response.status_code == 200
    assert response.data["total"] == 1
    assert response.data["results"][0]["sku"] == "TH-5001"


def test_a_typo_is_corrected_and_reported(client, catalog) -> None:
    response = client.get(SEARCH, {"q": "наутбук"})

    correction = response.data["correction"]
    assert correction["original"] == "наутбук"
    assert correction["corrected"] == "ноутбук"
    assert correction["changed"] is True
    assert correction["source"] == "dictionary"
    assert response.data["total"] == 1


def test_a_wrong_layout_query_is_corrected_and_reported(client, catalog) -> None:
    response = client.get(SEARCH, {"q": "rfhnhbl;"})

    correction = response.data["correction"]
    assert correction["corrected"] == "картридж"
    assert correction["source"] == "layout"
    assert response.data["total"] == 1


def test_correct_0_searches_exactly_what_was_typed(client, catalog) -> None:
    response = client.get(SEARCH, {"q": "наутбук", "correct": "0"})

    assert response.data["correction"]["changed"] is False
    assert response.data["correction"]["corrected"] == "наутбук"
    assert response.data["total"] == 0


def test_a_correct_query_reports_no_correction(client, catalog) -> None:
    response = client.get(SEARCH, {"q": "картридж"})

    assert response.data["correction"]["changed"] is False
    assert response.data["correction"]["source"] == "none"
    assert response.data["total"] == 1


def test_pagination_reports_the_full_total(client, catalog) -> None:
    response = client.get(SEARCH, {"limit": 2, "offset": 0})

    assert response.data["total"] == 3
    assert len(response.data["results"]) == 2
    assert response.data["has_more"] is True

    rest = client.get(SEARCH, {"limit": 2, "offset": 2})
    assert len(rest.data["results"]) == 1
    assert rest.data["has_more"] is False


def test_an_out_of_range_limit_is_refused(client, catalog) -> None:
    assert client.get(SEARCH, {"limit": 9999}).status_code == 400


def test_an_overlong_query_is_refused(client, catalog) -> None:
    assert client.get(SEARCH, {"q": "а" * 400}).status_code == 400


def test_a_product_payload_never_leaks_internals(client, catalog) -> None:
    product = client.get(SEARCH, {"q": "ноутбук"}).data["results"][0]

    # `search_vector` is an implementation detail of how search is indexed.
    assert set(product) == {
        "id",
        "sku",
        "name",
        "description",
        "category",
        "price",
        "price_label",
        "unit",
        "in_stock",
    }
