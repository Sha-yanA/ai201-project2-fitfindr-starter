"""
tests/test_tools.py

Pytest tests for all three FitFindr tools.
Each failure mode from planning.md has at least one dedicated test.

search_listings  - pure Python, no mocking needed.
suggest_outfit   - calls Groq; Groq client is mocked.
create_fit_card  - calls Groq; Groq client is mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from tools import create_fit_card, search_listings, suggest_outfit
from utils.data_loader import get_empty_wardrobe, get_example_wardrobe, load_listings


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_listing():
    """Return the first listing from the real dataset."""
    return load_listings()[0]


@pytest.fixture
def example_wardrobe():
    return get_example_wardrobe()


@pytest.fixture
def empty_wardrobe():
    return get_empty_wardrobe()


def _groq_mock(text="Great outfit suggestion here."):
    """Build a minimal Groq client mock that returns `text`."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = text
    return mock_client


# ── search_listings ────────────────────────────────────────────────────────────

class TestSearchListings:

    def test_returns_results_for_matching_query(self):
        results = search_listings("vintage graphic tee")
        assert len(results) > 0

    def test_returns_at_most_three_results(self):
        results = search_listings("vintage")
        assert len(results) <= 3

    def test_result_contains_required_fields(self):
        results = search_listings("jeans")
        assert len(results) > 0
        required = {"id", "title", "description", "category", "style_tags",
                    "size", "condition", "price", "colors", "brand", "platform"}
        for listing in results:
            assert required.issubset(listing.keys())

    # Failure mode: no results match the query
    def test_returns_empty_list_when_nothing_matches(self):
        """Nonsense query should yield no results - agent must handle [] gracefully."""
        results = search_listings("zzzzxxx_no_match_ever")
        assert results == []

    # Failure mode: max_price eliminates everything
    def test_returns_empty_list_when_price_ceiling_too_low(self):
        """Price cap of $0.01 should filter out every listing."""
        results = search_listings("vintage tee", max_price=0.01)
        assert results == []

    def test_price_filter_respected(self):
        """Every returned listing must be at or below max_price."""
        cap = 25.0
        results = search_listings("vintage", max_price=cap)
        for listing in results:
            assert listing["price"] <= cap

    def test_size_filter_respected(self):
        """Every returned listing must match the requested size token."""
        results = search_listings("top", size="S/M")
        for listing in results:
            tokens = listing["size"].upper().replace("/", " ").split()
            # query size "S/M" → both "S" and "M" are acceptable tokens
            assert any(t in tokens for t in ["S", "M", "S/M"])

    # Failure mode: size has no matches in dataset
    def test_returns_empty_list_when_size_has_no_matches(self):
        """Size "XXXXL" does not exist in the dataset."""
        results = search_listings("shirt", size="XXXXL")
        assert results == []

    def test_results_sorted_by_relevance(self):
        """Top result should contain at least one query keyword in title or tags."""
        results = search_listings("denim jacket")
        assert len(results) > 0
        top = results[0]
        text = (top["title"] + " " + " ".join(top["style_tags"])).lower()
        assert "denim" in text or "jacket" in text

    def test_does_not_raise_on_empty_description(self):
        """Empty query should return [] without raising."""
        results = search_listings("")
        assert isinstance(results, list)

    # Failure mode: runtime exception (file read / JSON parse)
    def test_returns_empty_list_on_data_load_error(self):
        """If load_listings() raises, search_listings must return [] without propagating."""
        with patch("tools.load_listings", side_effect=FileNotFoundError("listings.json not found")):
            result = search_listings("vintage tee")
        assert result == []


# ── suggest_outfit ─────────────────────────────────────────────────────────────

class TestSuggestOutfit:

    def test_returns_nonempty_string_with_wardrobe(self, sample_listing, example_wardrobe):
        """Happy path: returns a non-empty suggestion string."""
        mock_client = _groq_mock("Pair this with your baggy jeans and white sneakers.")
        with patch("tools._get_groq_client", return_value=mock_client):
            result = suggest_outfit(sample_listing, example_wardrobe)
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    # Failure mode: wardrobe is empty
    def test_returns_nonempty_string_when_wardrobe_is_empty(self, sample_listing, empty_wardrobe):
        """Empty wardrobe must NOT return '' or raise - should give generic styling tip."""
        mock_client = _groq_mock("This piece pairs well with high-waisted denim and white sneakers.")
        with patch("tools._get_groq_client", return_value=mock_client):
            result = suggest_outfit(sample_listing, empty_wardrobe)
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    def test_does_not_raise_when_wardrobe_is_empty(self, sample_listing, empty_wardrobe):
        """Empty wardrobe must not raise any exception."""
        mock_client = _groq_mock("Some generic styling advice.")
        with patch("tools._get_groq_client", return_value=mock_client):
            try:
                suggest_outfit(sample_listing, empty_wardrobe)
            except Exception as exc:
                pytest.fail(f"suggest_outfit raised unexpectedly: {exc}")

    def test_calls_llm_once(self, sample_listing, example_wardrobe):
        """The Groq API should be called exactly once per invocation."""
        mock_client = _groq_mock("An outfit suggestion.")
        with patch("tools._get_groq_client", return_value=mock_client):
            suggest_outfit(sample_listing, example_wardrobe)
        mock_client.chat.completions.create.assert_called_once()

    # Failure mode: new_item missing required fields
    def test_returns_fallback_when_new_item_missing_fields(self, example_wardrobe):
        """new_item missing required fields must return the explicit fallback message, not crash."""
        incomplete_item = {"title": "Mystery Item"}  # missing colors, style_tags, category
        result = suggest_outfit(incomplete_item, example_wardrobe)
        assert isinstance(result, str)
        assert "could not" in result.lower()

    # Failure mode: runtime exception (LLM / network)
    def test_returns_error_string_on_llm_failure(self, sample_listing, example_wardrobe):
        """If Groq raises, suggest_outfit must return an error string rather than propagating."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API unavailable")
        with patch("tools._get_groq_client", return_value=mock_client):
            result = suggest_outfit(sample_listing, example_wardrobe)
        assert isinstance(result, str)
        assert len(result.strip()) > 0


# ── create_fit_card ────────────────────────────────────────────────────────────

class TestCreateFitCard:

    def test_returns_nonempty_string_for_valid_inputs(self, sample_listing):
        """Happy path: returns a caption string."""
        mock_client = _groq_mock("thrifted this off depop for $22 and it slaps 🖤")
        with patch("tools._get_groq_client", return_value=mock_client):
            result = create_fit_card("Pair with baggy jeans.", sample_listing)
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    # Failure mode: outfit string is empty
    def test_calls_llm_once(self, sample_listing):
        """The Groq API should be called exactly once per invocation."""
        mock_client = _groq_mock("thrifted this off depop for $18 and it slaps")
        with patch("tools._get_groq_client", return_value=mock_client):
            create_fit_card("Pair with baggy jeans.", sample_listing)
        mock_client.chat.completions.create.assert_called_once()

    # Failure mode: outfit string is empty - early return, LLM never called
    def test_returns_error_string_when_outfit_is_empty(self, sample_listing):
        """Empty outfit must return a fallback string - NOT raise, NOT return ''."""
        result = create_fit_card("", sample_listing)
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    def test_does_not_call_llm_when_outfit_is_empty(self, sample_listing):
        """Empty outfit triggers early return - _get_groq_client should never be called."""
        with patch("tools._get_groq_client") as mock_get_client:
            create_fit_card("", sample_listing)
        mock_get_client.assert_not_called()

    def test_does_not_raise_when_outfit_is_whitespace_only(self, sample_listing):
        """Whitespace-only outfit string should be treated the same as empty."""
        result = create_fit_card("   ", sample_listing)
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    # Failure mode: runtime exception (LLM / network)
    def test_returns_error_string_on_llm_failure(self, sample_listing):
        """If Groq raises, create_fit_card must return an error string rather than propagating."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API unavailable")
        with patch("tools._get_groq_client", return_value=mock_client):
            result = create_fit_card("Some outfit suggestion.", sample_listing)
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    # Failure mode: new_item missing key fields
    def test_returns_fallback_when_new_item_missing_fields(self):
        """Incomplete new_item must return the fallback caption — LLM should not be called."""
        incomplete_item = {"title": "Mystery Item"}  # missing price, platform
        with patch("tools._get_groq_client") as mock_get_client:
            result = create_fit_card("Some outfit suggestion.", incomplete_item)
        mock_get_client.assert_not_called()
        assert "Mystery Item" in result
        assert "styled and ready to wear" in result.lower()
