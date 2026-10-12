#!/usr/bin/env python3
"""AO-MIMO-WIRING: the catalog guard for the Xiaomi MiMo Token Plan legs.

THE FIXTURE (`tests/fixtures/mimo-models.json`) IS THE GATEWAY'S VIEW OF PROVIDER
`xiaomi-mimo`, NOT THE VENDOR'S OWN /v1/models. The MiMo key lives only in the
OmniRoute gateway's DB, so every row here is what the gateway answers --
`GET /v1/models` for the ids and `GET /api/v1/providers/xiaomi-mimo/models` for
the capability fields -- plus the live-call findings those two lists do not show
(the 400 "not in the live catalog" cells and the 404 image-input cell). The
registry routes MiMo traffic THROUGH the gateway, so when the vendor's own
catalog and the gateway's disagree, the gateway is the source of truth for this
test -- a registry row the gateway cannot serve is a dead leg whatever the vendor
page says.

What it pins, each check its own function taking (registry, fixture) so a red
case is a mutated copy rather than a hand-edited registry file:

  (1) every MiMo model row the registry carries (found through the legs that
      resolve to the MiMo provider) names an id the fixture's `provider_models`
      answers -- an id the gateway does not list is an id it cannot serve;
  (2) no MiMo id sits in `RETIRED` (the dated table below): an id past its
      retirement answers 400 forever, so the table is the alarm. Read from the
      legs AND from `models`, since a retirement is a delete-the-row instruction
      and a leftover row with no leg is a half-done swap;
  (3) `context_advertised` / `output_max` equal the fixture's
      `context_length` / `max_output_tokens` for that id -- a row that promises
      more window than the gateway reports is a 413 waiting for the biggest
      card, and one that promises less clamps the combo for no reason;
  (4) `capabilities.image_in` does not contradict the fixture's `vision` cell:
      "proven" against vision false, or "broken" against vision true, is a
      capability claim the gateway's own row refutes;
  (5) every leg of every route that names a MiMo model resolves to a fixture id
      (the same fact as (1) read from the route side, so a failure names the
      route the resolver would send work to).

Stdlib-only, path-independent (ROOT from __file__), run directly -- like every
other suite in this repo (AGENTS.md section 5):
    python3 tests/test_mimo_catalog.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "mimo-models.json"


def _load_tool(name, path):
    """Import a tools/*.py by path (their names are not valid module ids)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry_tool = _load_tool("autoos_registry", ROOT / "tools" / "registry.py")

# The gateway connection id a MiMo leg carries (providers.<key>.omniroute_id).
GATEWAY_ID = "xiaomi-mimo"

# Model ids the gateway has said it stops serving, and when. Two spellings:
# an ISO date means "fails from that day on" (so a lane can record a scheduled
# retirement before it lands), null or absent means "fails now".
#
# The MiMo auto-route re-titles v2.5 -> v2.6 on 2026-10-14 and stops serving the
# two v2.5 chat ids on 2026-10-21 (fixture `_comment`, AO-MIMO-V26). The legs were
# swapped to mimo-v2.6-pro / mimo-v2.6-flash and the v2.5 chat rows deleted the day
# the swap landed, so nothing here fires today: the table is the alarm that keeps
# them deleted, and the fails-from-that-day-on spelling is what lets a lane record
# a scheduled retirement before it lands.
RETIRED: dict = {"mimo-v2.5-pro": "2026-10-21", "mimo-v2.5": "2026-10-21"}

# providers.<key> for the MiMo connection: the registry keys providers in
# snake_case and spells a leg with the gateway's own id, so both are looked up.
PROVIDER_KEY = "xiaomi_mimo"


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------


def load_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def load_registry(path=REGISTRY_PATH):
    return load_json(path)


def load_fixture(path=FIXTURE_PATH):
    return load_json(path)


# ---------------------------------------------------------------------------
# the MiMo slice of the registry
# ---------------------------------------------------------------------------


def mimo_provider_id(registry):
    """The registry provider key for the `xiaomi-mimo` gateway connection, or None.

    Matched on the key itself and on ``omniroute_id``, the same two ways
    tools/registry.py's ``resolve_leg()`` accepts a leg prefix."""
    for provider_id, entry in (registry.get("providers") or {}).items():
        if not isinstance(entry, dict):
            continue
        if provider_id == GATEWAY_ID or entry.get("omniroute_id") == GATEWAY_ID:
            return provider_id
    return None


def mimo_legs(registry, provider_id=None):
    """[(route_id, leg, model_id)] for every leg resolving to the MiMo provider.

    Read from the routes, which is where a leg exists for a resolver; a route's
    `unavailable_legs` keys count too -- a gated leg is still a row the registry
    claims the gateway serves, and tools/registry.py's own leg check reads them.
    """
    if provider_id is None:
        provider_id = mimo_provider_id(registry)
    out = []
    for route_id, route in (registry.get("routes") or {}).items():
        if not isinstance(route, dict):
            continue
        legs = list(route.get("legs") or [])
        legs += list((route.get("unavailable_legs") or {}).keys())
        for leg in dict.fromkeys(legs):
            try:
                leg_provider, model_id = registry_tool.resolve_leg(leg, registry)
            except ValueError:
                continue
            if leg_provider == provider_id:
                out.append((route_id, leg, model_id))
    return out


def mimo_models(registry, provider_id=None):
    """[(model_key, model_row)] for every models.<key> a MiMo leg resolves to."""
    if provider_id is None:
        provider_id = mimo_provider_id(registry)
    models = registry.get("models") or {}
    seen = []
    for _route_id, _leg, model_id in mimo_legs(registry, provider_id):
        if model_id in seen:
            continue
        seen.append(model_id)
    return [(key, models.get(key) or {}) for key in seen]


# ---------------------------------------------------------------------------
# the checks: each takes (registry, fixture) and returns problem strings
# ---------------------------------------------------------------------------


def fixture_rows(fixture):
    """{model id: row} for the fixture's `provider_models`."""
    return {row.get("id"): row for row in fixture.get("provider_models") or []
            if isinstance(row, dict) and row.get("id")}


def _fixture_row(fixture, model_id):
    """The fixture's own row object for `model_id`, so a red case can mutate the
    gateway side instead of the registry side."""
    for row in fixture.get("provider_models") or []:
        if isinstance(row, dict) and row.get("id") == model_id:
            return row
    raise AssertionError("the synthetic fixture carries no %r row" % model_id)


def check_provider_present(registry, fixture):
    """The registry must actually carry the provider this fixture describes.

    Without it there is nothing to check, and a silent pass would be the worst
    outcome a catalog guard can have: the legs would simply never exist."""
    if mimo_provider_id(registry) is None:
        return ["registry carries no provider the gateway spells %r "
                "(no providers.<key>.omniroute_id matches it), so no MiMo leg "
                "can resolve" % GATEWAY_ID]
    return []


def check_model_ids(registry, fixture):
    """(1) every MiMo model row names an id the gateway's catalog answers."""
    known = set(fixture_rows(fixture))
    problems = []
    for model_id, _row in mimo_models(registry):
        if model_id not in known:
            problems.append(
                "models.%s: not in the gateway fixture's provider_models "
                "(the gateway lists %s)" % (model_id, ", ".join(sorted(known)) or "nothing"))
    return problems


def check_retired_ids(registry, fixture, retired=None, today=None):
    """(2) the registry carries no id the gateway has said it stops serving.

    Read from both sides, since a retirement is a delete-the-row instruction and not
    only a re-leg one: a leg that would route work to a retired id (the route names
    it, which is where the resolver reads), and a `models.<key>` row for one — a
    row with no leg is still a row claiming the gateway serves that id, and it is
    what `render ide` / `render models-doc` would publish (AO-MIMO-V26)."""
    if retired is None:
        retired = RETIRED
    problems = []
    for _route_id, leg, model_id in mimo_legs(registry):
        if model_id not in retired:
            continue
        until = retired[model_id]
        if until is None or str(today or _today()) >= str(until):
            problems.append(
                "leg %r is model %r, retired%s -- the gateway answers 400 for a "
                "retired id; swap the leg and refresh the fixture"
                % (leg, model_id, " on %s" % until if until is not None else ""))
    for model_id in sorted(registry.get("models") or {}):
        if model_id not in retired:
            continue
        if any(model_id == mid for _r, _l, mid in mimo_legs(registry)):
            continue                      # the leg line above already named it
        until = retired[model_id]
        if until is None or str(today or _today()) >= str(until):
            problems.append(
                "models.%s is retired%s -- no leg routes to it, but the registry "
                "still carries a row for an id the gateway answers 400 for; delete "
                "the row" % (model_id, " on %s" % until if until is not None else ""))
    return problems


def check_windows(registry, fixture):
    """(3) the row's context/output numbers are the gateway's own."""
    problems = []
    rows = fixture_rows(fixture)
    for model_id, row in mimo_models(registry):
        fixture_row = rows.get(model_id)
        if fixture_row is None:
            continue  # check_model_ids names it
        for field, fixture_field in (("context_advertised", "context_length"),
                                     ("output_max", "max_output_tokens")):
            expected = fixture_row.get(fixture_field)
            if expected is None:
                continue  # the fixture states nothing about this id's field
            got = row.get(field)
            if got != expected:
                problems.append(
                    "models.%s.%s is %r, the gateway fixture reports %s %r"
                    % (model_id, field, got, fixture_field, expected))
    return problems


def check_image_capability(registry, fixture):
    """(4) capabilities.image_in agrees with the fixture's `vision` cell.

    Only the two contradiction pairs are named: `unproven` (and an absent
    capabilities block) is what the registry says when nothing answered, which
    cannot contradict a vision cell the smoke never exercised."""
    problems = []
    rows = fixture_rows(fixture)
    for model_id, row in mimo_models(registry):
        fixture_row = rows.get(model_id)
        if fixture_row is None:
            continue  # check_model_ids names it
        vision = (fixture_row.get("capabilities") or {}).get("vision")
        verdict = (row.get("capabilities") or {}).get("image_in")
        if verdict == "proven" and vision is False:
            problems.append(
                "models.%s.capabilities.image_in is \"proven\" but the fixture's "
                "vision cell is false (no image endpoint answered)" % model_id)
        elif verdict == "broken" and vision is True:
            problems.append(
                "models.%s.capabilities.image_in is \"broken\" but the fixture's "
                "vision cell is true (the gateway advertises image input)" % model_id)
    return problems


def check_route_legs(registry, fixture):
    """(5) a route never points at a MiMo id the gateway does not list."""
    known = set(fixture_rows(fixture))
    problems = []
    for route_id, leg, model_id in mimo_legs(registry):
        if model_id not in known:
            problems.append(
                "routes.%s leg %r names MiMo model %r, absent from the gateway "
                "fixture's provider_models" % (route_id, leg, model_id))
    return problems


def check_chat_callable(registry, fixture):
    """A MiMo row the gateway refuses to chat with is not a leg at all.

    The fixture's `live_call_results` records what an actual chat call answered;
    an id whose `chat` cell says 400 is listed by the gateway but not in its
    active live catalog, so a leg on it fails every card that reaches it."""
    problems = []
    live = fixture.get("live_call_results") or {}
    for _route_id, leg, model_id in mimo_legs(registry):
        cell = live.get(model_id)
        if not isinstance(cell, dict):
            continue
        chat = str(cell.get("chat") or "")
        if chat.startswith("4"):
            problems.append(
                "leg %r is not chat-callable through the gateway: the fixture "
                "records chat %r for %s" % (leg, chat, model_id))
    return problems


CHECKS = (check_provider_present, check_model_ids, check_retired_ids,
          check_windows, check_image_capability, check_route_legs,
          check_chat_callable)


def catalog_problems(registry, fixture, retired=None, today=None):
    """Every MiMo catalog problem, each check contributing its own lines."""
    problems = []
    for check in CHECKS:
        if check is check_retired_ids:
            problems.extend(check(registry, fixture, retired=retired, today=today))
        else:
            problems.extend(check(registry, fixture))
    return problems


def _today():
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# a synthetic registry+fixture pair for the red cases
# ---------------------------------------------------------------------------


def synth_registry():
    """The shape the real registry has for MiMo, at the smallest size that still
    exercises every check: one provider row, three model rows (two legged, one
    row-only like the shipped asr id), one vertex row to fall through to."""
    return {
        "version": "2026-10-12",
        "providers": {
            PROVIDER_KEY: {
                "id": PROVIDER_KEY,
                "omniroute_id": GATEWAY_ID,
                "litellm_env": None,
                "litellm_prefix": None,
                "api_base": "https://token-plan-ams.xiaomimimo.com/v1",
                "tier": "subscription",
                "trains_on_prompts": None,
            },
            "vertex_ai": {"id": "vertex_ai", "omniroute_id": "vertex",
                          "litellm_env": None, "litellm_prefix": None,
                          "api_base": None, "tier": "credit",
                          "trains_on_prompts": False},
        },
        "models": {
            "mimo-v2.6-pro": {
                "id": "mimo-v2.6-pro", "family": "xiaomi",
                "context_advertised": 1048576,
                "context_usable": {"tokens": 524288, "source": "default"},
                "output_max": 131072, "reasoning": True, "effort_ladder": [],
                "tool_calls": "proven", "price_in": 0.0, "price_out": 0.0,
                "prompt_cache": "true",
                "capabilities": {"text": "proven", "image_in": "proven",
                                 "audio_in": "proven", "audio_out": "unproven",
                                 "video_in": "unproven"},
            },
            "mimo-v2.6-flash": {
                "id": "mimo-v2.6-flash", "family": "xiaomi",
                "context_advertised": 1048576,
                "context_usable": {"tokens": 524288, "source": "default"},
                "output_max": 131072, "reasoning": True, "effort_ladder": [],
                "tool_calls": "proven", "price_in": 0.0, "price_out": 0.0,
                "prompt_cache": "true",
                "capabilities": {"text": "proven", "image_in": "proven"},
            },
            "mimo-v2.5-asr": {
                "id": "mimo-v2.5-asr", "family": "xiaomi",
                "context_advertised": 8192,
                "context_usable": {"tokens": 4096, "source": "default"},
                "output_max": 2048, "reasoning": True, "effort_ladder": [],
                "tool_calls": "unproven", "price_in": 0.0, "price_out": 0.0,
                "prompt_cache": "unknown",
                "capabilities": {"text": "unproven", "image_in": "unproven",
                                 "audio_in": "proven"},
            },
            "gemini-3.8-flash": {
                "id": "gemini-3.8-flash", "family": "google",
                "context_advertised": 1048576,
                "context_usable": {"tokens": 1048576, "source": "default"},
                "output_max": 65536, "reasoning": True, "effort_ladder": [],
                "tool_calls": "proven", "price_in": 0.0, "price_out": 0.0,
            },
        },
        "routes": {
            "l2-worker": {"id": "l2-worker", "class": "mid", "strategy": "priority",
                          "legs": ["xiaomi-mimo/mimo-v2.6-pro"]},
            "l3-driver": {"id": "l3-driver", "class": "cheap", "strategy": "priority",
                          "legs": ["xiaomi-mimo/mimo-v2.6-flash",
                                   "vertex/gemini-3.8-flash"]},
        },
    }


def synth_fixture():
    """The gateway's answer for the same ids, in the fixture's own shape: the two
    v2.6 chat rows advertise vision, the asr row does not, and the v2.5 chat ids
    are still listed (they retire 2026-10-21, not before)."""
    return {
        "captured": "2026-10-12",
        "gateway_v1_models_ids": [
            "xiaomi-mimo/mimo-v2.5", "xiaomi-mimo/mimo-v2.5-asr",
            "xiaomi-mimo/mimo-v2.5-pro", "xiaomi-mimo/mimo-v2.6-flash",
            "xiaomi-mimo/mimo-v2.6-pro"],
        "provider_models": [
            {"id": "mimo-v2.6-pro",
             "capabilities": {"vision": True, "tool_calling": True, "reasoning": True},
             "context_length": 1048576, "max_output_tokens": 131072},
            {"id": "mimo-v2.6-flash",
             "capabilities": {"vision": True, "tool_calling": True, "reasoning": True},
             "context_length": 1048576, "max_output_tokens": 131072},
            {"id": "mimo-v2.5-asr",
             "capabilities": {"vision": False, "tool_calling": True, "reasoning": True},
             "context_length": 8192, "max_output_tokens": 2048},
            {"id": "mimo-v2.5-pro",
             "capabilities": {"vision": False, "tool_calling": True, "reasoning": True},
             "context_length": 1048576, "max_output_tokens": 131072},
            {"id": "mimo-v2.5",
             "capabilities": {"vision": True, "tool_calling": True, "reasoning": True},
             "context_length": 1048576, "max_output_tokens": 131072},
        ],
        "live_call_results": {
            "mimo-v2.6-pro": {"text": "200", "tools": "200 tool_call",
                              "image_in": "200 image_tokens 9"},
            "mimo-v2.6-flash": {"text": "200", "tools": "200 tool_call",
                                "image_in": "200 image_tokens 9"},
            "mimo-v2.5-asr": {"audio_in": "200 audio_tokens 7"},
        },
    }


def mutate(changes):
    """A deep copy of the synthetic pair with `changes(registry, fixture)` applied."""
    registry, fixture = synth_registry(), synth_fixture()
    changes(registry, fixture)
    return registry, fixture


# ---------------------------------------------------------------------------
# the real files
# ---------------------------------------------------------------------------


class MimoRegistryAgainstFixture(unittest.TestCase):
    """The guard itself: the shipped registry against the shipped fixture."""

    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.fixture = load_fixture()

    def test_the_shipped_registry_matches_the_gateway_fixture(self):
        problems = catalog_problems(self.registry, self.fixture)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_both_v2_6_legs_ride_the_coding_combos(self):
        """AO-MIMO-WIRING placement, re-leged to the v2.6 ids by AO-MIMO-V26: each
        MiMo leg closes its chain — behind the vertex credit leg in both. The brief
        asked for "after the free legs, before vertex"; contract rule (b) ranks
        `subscription` with `paid` (tools/combo-contract.py TIER_ORDER) and
        tools/autoos_resolver.py holds a non-free tier back as a last resort while a
        free/trial/credit leg is healthy, so the tail is the only position the shipped
        spend model admits. The tail is unchanged by the swap: same index, new id.
        The MiMo ToS allows coding tools only, so these two ids and no others.

        And with D-1181's `providers.vertex_ai.available: false`, the vertex leg is
        declared but dropped from every render, which makes the MiMo leg the last
        SERVED leg of both chains — the fact the second half of this test pins."""
        legs = self.registry["routes"]["l2-worker"]["legs"]
        self.assertIn("xiaomi-mimo/mimo-v2.6-pro", legs)
        self.assertLess(legs.index("openrouter/nvidia/nemotron-3-super-120b-a12b:free"),
                        legs.index("xiaomi-mimo/mimo-v2.6-pro"))
        self.assertEqual(legs[-2:], ["vertex/gemini-3.8-flash",
                                     "xiaomi-mimo/mimo-v2.6-pro"])
        legs = self.registry["routes"]["l3-driver"]["legs"]
        self.assertIn("xiaomi-mimo/mimo-v2.6-flash", legs)
        self.assertLess(legs.index("cohere/command-a-plus-05-2026"),
                        legs.index("xiaomi-mimo/mimo-v2.6-flash"))
        self.assertEqual(legs[-3:], ["ovhcloud/Qwen3.5-397B-A17B",
                                     "vertex/gemini-3.8-flash",
                                     "xiaomi-mimo/mimo-v2.6-flash"])
        for route_id, leg in (("l2-worker", "xiaomi-mimo/mimo-v2.6-pro"),
                              ("l3-driver", "xiaomi-mimo/mimo-v2.6-flash")):
            served = registry_tool.gateway_legs(self.registry["routes"][route_id],
                                                self.registry)
            self.assertEqual(served[-1], leg,
                             "%s: %r is not the last leg the gateway can actually "
                             "serve (vertex is off)" % (route_id, leg))

    def test_the_legs_reach_no_route_beyond_the_two_coding_combos(self):
        """The Token Plan permits coding tools and bans "clearly non-coding
        scenarios", so no research/chat/TTS route may carry a MiMo leg -- and a
        -clean route may not either, since trains_on_prompts is null (unverified)
        and tools/registry.py's private_safe() fails closed on null."""
        seen = {}
        for route_id, leg, _model_id in mimo_legs(self.registry):
            seen.setdefault(leg, set()).add(route_id)
        self.assertEqual(
            {leg: sorted(routes) for leg, routes in seen.items()
             if routes - {"l2-worker", "l3-driver"}}, {},
            "a MiMo leg rides a route the ToS does not permit")
        self.assertEqual(
            {leg: sorted(routes) for leg, routes in seen.items()},
            {"xiaomi-mimo/mimo-v2.6-pro": ["l2-worker"],
             "xiaomi-mimo/mimo-v2.6-flash": ["l3-driver"]})

    def test_the_asr_id_carries_a_row_and_no_leg(self):
        """mimo-v2.5-asr answers 200 for an `input_audio` part and nothing else —
        its `text` cell is unproven and it takes 8192 of window — so it is a row the
        catalog keeps for the record and not a leg any tier route may carry."""
        self.assertIn("mimo-v2.5-asr", self.registry["models"])
        self.assertEqual([leg for _r, leg, mid in mimo_legs(self.registry)
                          if mid == "mimo-v2.5-asr"], [])


class RetiredTableChecker(unittest.TestCase):
    """(2): the table the shipped registry is checked against, and the mechanism
    proven on synthetic data — a mutated copy that carries a retired id, since
    nothing in the shipped registry may.

    The v2.5 chat ids retire 2026-10-21 (v2.5 -> v2.6 auto-route on 2026-10-14), so
    the date has not passed at commit time and the date gate alone would stay quiet
    even on a wrong registry. The shipped test therefore asserts the registry carries
    neither id outright, whatever the date, which is what the swap was for."""

    def _plant(self, model_id, leg_route="l2-worker"):
        """A synth copy carrying a retired id as both a row and a leg — the shape the
        check must name, which the shipped registry no longer has."""
        registry, fixture = synth_registry(), synth_fixture()
        registry["models"][model_id] = {"id": model_id}
        registry["routes"][leg_route]["legs"].insert(
            0, "%s/%s" % (GATEWAY_ID, model_id))
        return registry, fixture

    def test_the_table_names_the_two_retiring_chat_ids(self):
        self.assertEqual(RETIRED, {"mimo-v2.5-pro": "2026-10-21",
                                   "mimo-v2.5": "2026-10-21"})

    def test_the_shipped_registry_carries_neither_retired_id(self):
        """No leg and no model row for an id the gateway retires — read with
        `today` forced to the retirement date, so this fires on a half-done swap
        (legs moved, rows left behind) instead of waiting for 2026-10-21."""
        registry = load_registry()
        for model_id in sorted(RETIRED):
            self.assertNotIn(model_id, registry.get("models") or {},
                             "models.%s is retired 2026-10-21; delete the row" % model_id)
        problems = check_retired_ids(registry, load_fixture(), retired=RETIRED,
                                     today="2026-10-21")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_retired_id_with_a_passed_date_is_named(self):
        registry, fixture = self._plant("mimo-v2.5")
        problems = check_retired_ids(registry, fixture, retired=RETIRED,
                                     today="2026-10-22")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("mimo-v2.5", problems[0])
        self.assertIn("retired on 2026-10-21", problems[0])

    def test_a_retirement_recorded_early_does_not_fire_early(self):
        registry, fixture = self._plant("mimo-v2.5")
        self.assertEqual(check_retired_ids(registry, fixture, retired=RETIRED,
                                          today="2026-10-12"), [])

    def test_a_dateless_entry_fires_immediately(self):
        registry, fixture = self._plant("mimo-v2.5-pro")
        problems = check_retired_ids(registry, fixture, retired={"mimo-v2.5-pro": None})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("xiaomi-mimo/mimo-v2.5-pro", problems[0])

    def test_a_retired_row_with_no_leg_is_still_named(self):
        """A leftover model row is the half-done swap: no route points at it, but the
        registry still claims the gateway serves an id it answers 400 for."""
        registry, fixture = synth_registry(), synth_fixture()
        registry["models"]["mimo-v2.5-pro"] = {"id": "mimo-v2.5-pro"}
        problems = check_retired_ids(registry, fixture, retired=RETIRED,
                                     today="2026-10-21")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("models.mimo-v2.5-pro", problems[0])
        self.assertIn("delete the row", problems[0])


class RedCaseCheckerFunctions(unittest.TestCase):
    """Each check fires, and names what it fired on, against a mutated copy."""

    def test_clean_synthetic_pair_reports_nothing(self):
        self.assertEqual(catalog_problems(synth_registry(), synth_fixture()), [])

    def test_absent_provider_is_named(self):
        registry, fixture = mutate(
            lambda reg, fx: reg["providers"].pop(PROVIDER_KEY))
        problems = catalog_problems(registry, fixture)
        self.assertTrue(any(GATEWAY_ID in p for p in problems), problems)

    def test_model_id_missing_from_the_fixture_is_named(self):
        registry, fixture = mutate(
            lambda reg, fx: reg["models"].__setitem__("mimo-v9.9-turbo", dict(
                reg["models"]["mimo-v2.6-flash"], id="mimo-v9.9-turbo")))
        registry["routes"]["l2-worker"]["legs"] = ["xiaomi-mimo/mimo-v9.9-turbo"]
        problems = check_model_ids(registry, fixture)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("mimo-v9.9-turbo", problems[0])
        self.assertIn("provider_models", problems[0])

    def test_a_retired_id_left_in_the_registry_is_named_by_the_missing_check_too(self):
        """The shipped fixture still lists the v2.5 chat ids until 2026-10-21, so a
        leftover row is not a missing-from-fixture problem — RETIRED is the only
        check that sees it. Pinned here so nobody "fixes" the fixture by deleting
        those rows and calls the guard sufficient."""
        registry, fixture = self._plant_rows()
        self.assertEqual(check_model_ids(registry, fixture), [])
        self.assertTrue(check_retired_ids(registry, fixture, retired=RETIRED,
                                         today="2026-10-21"))

    def _plant_rows(self):
        registry, fixture = synth_registry(), synth_fixture()
        for model_id in sorted(RETIRED):
            registry["models"][model_id] = {"id": model_id}
        return registry, fixture

    def test_a_route_leg_naming_an_unknown_mimo_id_is_named_with_its_route(self):
        registry, fixture = mutate(
            lambda reg, fx: reg["models"].__setitem__("mimo-v9.9-turbo", dict(
                reg["models"]["mimo-v2.6-flash"], id="mimo-v9.9-turbo")))
        registry["routes"]["l3-driver"]["legs"].insert(
            0, "xiaomi-mimo/mimo-v9.9-turbo")
        problems = check_route_legs(registry, fixture)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("routes.l3-driver", problems[0])
        self.assertIn("mimo-v9.9-turbo", problems[0])

    def test_a_window_that_disagrees_with_the_gateway_is_named(self):
        registry, fixture = mutate(
            lambda reg, fx: reg["models"]["mimo-v2.6-pro"].__setitem__(
                "context_advertised", 131072))
        problems = check_windows(registry, fixture)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("context_advertised", problems[0])
        self.assertIn("1048576", problems[0])

    def test_an_output_max_that_disagrees_with_the_gateway_is_named(self):
        registry, fixture = mutate(
            lambda reg, fx: reg["models"]["mimo-v2.6-flash"].__setitem__(
                "output_max", 32768))
        problems = check_windows(registry, fixture)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("output_max", problems[0])

    def test_image_in_proven_against_vision_false_is_named(self):
        """The row claims image input; the gateway's own row for that id says it has
        no vision. Proven either way — the shipped v2.6 rows answer image_tokens 200,
        so the contradiction is created on the fixture side."""
        registry, fixture = mutate(
            lambda reg, fx: _fixture_row(fx, "mimo-v2.6-pro")["capabilities"]
            .__setitem__("vision", False))
        problems = check_image_capability(registry, fixture)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("mimo-v2.6-pro", problems[0])
        self.assertIn("vision cell is false", problems[0])

    def test_image_in_broken_against_vision_true_is_named(self):
        registry, fixture = mutate(
            lambda reg, fx: reg["models"]["mimo-v2.6-flash"]["capabilities"].__setitem__(
                "image_in", "broken"))
        problems = check_image_capability(registry, fixture)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("mimo-v2.6-flash", problems[0])
        self.assertIn("vision cell is true", problems[0])

    def test_an_unproven_capability_contradicts_nothing(self):
        registry, fixture = mutate(
            lambda reg, fx: reg["models"]["mimo-v2.6-flash"]["capabilities"].__setitem__(
                "image_in", "unproven"))
        self.assertEqual(check_image_capability(registry, fixture), [])

    def test_a_leg_on_an_id_the_gateway_refuses_to_chat_is_named(self):
        registry, fixture = mutate(
            lambda reg, fx: fx["live_call_results"]["mimo-v2.6-pro"].__setitem__(
                "chat", "400 not in live catalog"))
        problems = check_chat_callable(registry, fixture)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("xiaomi-mimo/mimo-v2.6-pro", problems[0])

    def test_absent_capabilities_block_is_not_a_contradiction(self):
        """The field is optional: a row that says nothing claims nothing."""
        registry, fixture = mutate(
            lambda reg, fx: reg["models"]["mimo-v2.6-flash"].pop("capabilities"))
        self.assertEqual(check_image_capability(registry, fixture), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
