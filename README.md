# FitFindr

A thrift-shopping assistant that takes a natural language clothing query and returns a styled outfit recommendation sourced from secondhand listings.

---

## What's Included

```
ai201-project2-fitfindr-starter/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe format + example wardrobe
├── tests/
│   └── test_tools.py          # Unit tests for all three tools (Groq mocked)
├── utils/
│   └── data_loader.py         # Helper functions for loading the data
├── tools.py                   # The three FitFindr tools
├── agent.py                   # Planning loop: orchestrates the tools
├── app.py                     # Gradio UI
├── conftest.py                # pytest config: adds project root to sys.path
├── verify.py                  # End-to-end acceptance tests (requires Groq API key)
├── planning.md                # Spec and design decisions
└── requirements.txt           # Python dependencies
```

---

## Setup

```bash
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file (free key at [console.groq.com](https://console.groq.com)):
```
GROQ_API_KEY=your_key_here
```

Run the Gradio UI:
```bash
python app.py
```

Run unit tests (no API key required, Groq is mocked):
```bash
pytest -v
```

Run end-to-end acceptance tests against the real API:
```bash
python verify.py
```

---

## Tool Inventory

### Tool 1: `search_listings`

**Purpose:** Filters the mock listings dataset against the user's query and returns a ranked list of matching secondhand items.

**Inputs:**
- `description` (`str`) - free-text query (e.g. `"vintage graphic tee"`)
- `size` (`str | None`) - size token to filter by (e.g. `"M"`, `"W30"`); `None` skips size filtering
- `max_price` (`float | None`) - upper price limit inclusive; `None` skips price filtering

**Output:** `list[dict]` : up to 3 matching listing dicts sorted by relevance score (descending). Returns `[]` if nothing matches - never raises.

Each dict contains: `id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`.

**Scoring:** Weighted keyword overlap - title (0.5), description (0.3), style_tags (0.15), category (0.05). Ties broken by lower price then better condition.

---

### Tool 2: `suggest_outfit`

**Purpose:** Given a thrifted item and the user's wardrobe, generates a natural-language outfit suggestion pairing the new item with pieces the user already owns.

**Inputs:**
- `new_item` (`dict`) - listing dict from `search_listings`; must include `title`, `colors`, `style_tags`, `category`
- `wardrobe` (`dict`) - wardrobe dict with an `items` key; may be empty

**Output:** `str` : 2–4 sentence outfit suggestion. If `wardrobe["items"]` is empty, returns a 1–2 sentence generic styling tip based solely on the new item's tags and colors. Never returns an empty string or raises.

---

### Tool 3: `create_fit_card`

**Purpose:** Produces a short, social-media-style caption summarizing the thrifted find and the outfit.

**Inputs:**
- `outfit` (`str`) - outfit suggestion string from `suggest_outfit`
- `new_item` (`dict`) - listing dict; used for `title`, `price`, `platform`, `condition`

**Output:** `str` : 2–4 sentence Instagram/TikTok-style caption in a casual first-person voice. Mentions item name, price, and platform naturally. If `outfit` is empty or `new_item` is missing required fields (`title`, `price`, `platform`), returns a minimal fallback caption instead of raising.

---

## Planning Loop

The agent runs a sequential pipeline with an early exit if no listings are found:

1. **Parse** the user's query with regex to extract `description`, `size`, and `max_price`. Price is stripped first so digits in `"$30"` can't be misread as a size.
2. **Search** : call `search_listings(description, size, max_price)`.
3. **Early exit** - if results is `[]`, set `session["error"]` to a user-facing message and return immediately. `suggest_outfit` and `create_fit_card` are never called.
4. **Select** : `session["selected_item"] = results[0]`.
5. **Outfit** : call `suggest_outfit(selected_item, wardrobe)`, store in `session["outfit_suggestion"]`.
6. **Fit card** : call `create_fit_card(outfit_suggestion, selected_item)`, store in `session["fit_card"]`.
7. **Return** the completed session dict.

---

## State Management

All state lives in a single session dict created at the start of each interaction:

| Key | Set by | Passed to |
|---|---|---|
| `query` | `run_agent` input | - |
| `parsed` | Step 2 (regex) | `search_listings` |
| `search_results` | `search_listings` | - |
| `selected_item` | `results[0]` | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | `run_agent` input | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI / caller |
| `error` | Early-exit guard | UI / caller |

State flows strictly forward. Each tool reads only what was set by the previous step, there is no re-prompting the user or regenerating values mid-pipeline.

---

## Error Handling

| Tool | Failure mode | What happens |
|---|---|---|
| `search_listings` | No results match query | `session["error"]` set to a descriptive message; pipeline stops; `suggest_outfit` and `create_fit_card` never called |
| `search_listings` | `load_listings()` raises (file missing, JSON corrupt) | Exception caught; returns `[]`; agent treats it the same as no results and sets error |
| `suggest_outfit` | `wardrobe["items"]` is empty | Calls LLM with a general styling prompt instead of a pairing prompt; returns 1–2 sentence tip; pipeline continues normally |
| `suggest_outfit` | `new_item` missing required fields | Returns `"Could not generate a full outfit suggestion - new item is missing key fields."` immediately; no LLM call |
| `suggest_outfit` | LLM / network exception | Exception caught; returns a user-facing error string; `create_fit_card` still receives it and produces a fallback |
| `create_fit_card` | `outfit` is empty or whitespace | Returns `"Found {title} on {platform} - styled and ready to wear."` immediately; no LLM call |
| `create_fit_card` | `new_item` missing `title`/`price`/`platform` | Same fallback as above; LLM not called |
| `create_fit_card` | LLM / network exception | Exception caught; returns a user-facing error string |

**Concrete example tested:**
- Query `"designer ballgown size XXS under $5"` → `search_listings` returns `[]` → `session["error"] = "No listings matched 'designer ballgown' under $5 in size XXS. Try a broader description, a higher budget, or remove the size filter."` → `session["fit_card"] = None`, `session["outfit_suggestion"] = None` The downstream tools were never reached.

---

## AI Usage

### Instance 1: Implementing `search_listings`

**What I gave Claude:** The Tool 1 spec block from `planning.md` (inputs, return value, scoring weights, size matching rules, failure mode) plus the `load_listings()` docstring from `utils/data_loader.py`.

**What it produced:** A complete `search_listings` implementation with weighted keyword scoring across four fields, size tokenization (`"S/M"` → `["S", "M"]`), numeric size relaxation (`"30"` matches `"W30"`), and a try/except that returns `[]` on any exception.

**What I changed:** The initial implementation used a flat keyword match with no weighting. I overrode it to match the exact weights in `planning.md` (title: 0.5, description: 0.3, style_tags: 0.15, category: 0.05) and added the tie-breaking logic (lower price, then better condition) which was in the spec but missing from the generated code.

---

### Instance 2: Implementing `suggest_outfit` and `create_fit_card`

**What I gave Claude:** The Tool 2 and Tool 3 spec blocks from `planning.md`, the wardrobe schema, and the sentence-count enforcement helpers (`_enforce_sentence_count`, `_split_sentences`, `_simple_fallback_sentence_for_item`) I had already written.

**What it produced:** Both tools with correct LLM prompts, the empty-wardrobe branch, the missing-fields guard in `create_fit_card`, and calls to `_enforce_sentence_count` on every return path.

**What I changed:** Claude initially generated `suggest_outfit` with both the empty-wardrobe and full-wardrobe paths enforcing `(raw, 2, 4, ...)`, treating them identically. I overrode this to use `(raw, 1, 2, ...)` on the empty-wardrobe path, keeping it shorter since a generic styling tip is expected to shorter than a specific wardrobe pairing. I also found that the `create_fit_card` missing-fields guard was absent from the first generated version, it only handled the empty `outfit` case. I added the `required_item_fields` check explicitly and updated the corresponding unit test to assert that `_get_groq_client` is never called on that path.
