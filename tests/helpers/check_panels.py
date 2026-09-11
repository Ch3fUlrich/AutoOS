#!/usr/bin/env python3
"""Keep each browser-UI panel to one job.

Overview grew to six cards covering three unrelated questions — what this machine
is, what to install, and how the installed things are configured — and the
read-only hardware inventory sat in the middle of the install flow. The split is
only worth making if it stays made, so this asserts where each card lives and
that the JS card map agrees with the markup.

Exit 0 when the layout holds, 1 (with the difference on stderr) when it does not.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

EXPECTED = {
    "overview": ["cardProfile", "cardCatalog", "cardOrder"],
    "system": ["cardSystem"],
    "configure": ["questionsCard", "cardConfig", "cardClaudeAutostart"],
}


def panels(page):
    """Which card ids appear inside each <section role="region" id="panel-*">.

    They were `role="tabpanel"` until the tab strip became a dropdown in the
    header; with no tablist left, tabpanel was the wrong role.
    """
    found = {}
    for match in re.finditer(r'<section role="region" id="panel-([a-z]+)"', page):
        name = match.group(1)
        start = match.end()
        nxt = page.find('<section role="region"', start)
        body = page[start:nxt if nxt != -1 else len(page)]
        found[name] = re.findall(r'<details class="card" id="([A-Za-z]+)"', body)
    return found


def card_tab_map(page):
    match = re.search(r"const CARD_TAB = \{(.*?)\};", page, re.S)
    if not match:
        print("web/index.html does not declare CARD_TAB", file=sys.stderr)
        sys.exit(1)
    return dict(re.findall(r"(\w+):\s*\"(\w+)\"", match.group(1)))


def main():
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    found, mapping = panels(page), card_tab_map(page)
    problems = []

    for panel, cards in EXPECTED.items():
        actual = found.get(panel)
        if actual is None:
            problems.append(f"panel-{panel} is missing")
        elif actual != cards:
            problems.append(f"panel-{panel} holds {actual}, expected {cards}")

    # A Configure link that switches to the wrong tab scrolls a panel nobody is
    # looking at, so the map has to match where the cards actually are.
    for panel, cards in EXPECTED.items():
        for card in cards:
            if mapping.get(card) != panel:
                problems.append(f"CARD_TAB[{card}] is {mapping.get(card)!r}, but it lives on {panel}")

    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
