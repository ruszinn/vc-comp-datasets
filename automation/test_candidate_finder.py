"""Offline tests for candidate_finder — no network, no API key, no cost.

The finder's whole job is deciding what to TRUST, so the parts worth testing are
the gates, not the plumbing. Both external dependencies are injected: the web is
a dict of fake pages (extract.fetch), and the Claude call is replaced with a
fixed list of proposals. That makes every gate reachable deterministically,
including the ones a live run would rarely hit (parked domain, real firm paired
with the wrong domain, a JS shell with no text to match).

The queue file is redirected to a temp dir, so running this never touches
data/discovered_candidates.json.

Run:  python3 automation/test_candidate_finder.py     (exit 0 = all pass)
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import candidate_finder as cf  # noqa: E402
import identity  # noqa: E402
import extract  # noqa: E402
import roster  # noqa: E402

_FAILS: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        _FAILS.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   got={got!r} want={want!r}"))


def page(title: str, body: str = "Portfolio of venture investments in founders.") -> str:
    """A page with enough text that the JS-shell fallback does NOT apply."""
    return (f"<html><head><title>{title}</title></head><body><p>{body}</p>"
            + "lorem ipsum " * 60 + "</body></html>")


# --------------------------------------------------------------- domain parsing
def test_norm_domain() -> None:
    print("\n_norm_domain")
    check("strips scheme/www/path", cf._norm_domain("https://www.Example.VC/portfolio"),
          "example.vc")
    check("bare domain passes through", cf._norm_domain("example.vc"), "example.vc")
    check("strips port", cf._norm_domain("http://foo.co.uk:443/x"), "foo.co.uk")
    check("rejects non-domain", cf._norm_domain("not a url"), "")
    check("rejects empty", cf._norm_domain(""), "")


# ------------------------------------------------------------- the identity gate
def test_name_present() -> None:
    print("\n_name_present  (page evidence required; domain is not evidence)")
    check("full name in title",
          cf._name_present("Emergence Capital", "emcap.com", page("Emergence Capital")),
          True)
    check("distinctive token in body",
          cf._name_present("Emergence Capital", "emcap.com",
                           page("Home", "emergence backs enterprise founders")),
          True)
    # the gate that matters: a made-up firm arrives with a domain spun from its
    # own name, so the domain must not be allowed to vouch for the page.
    check("unrelated site, self-referential domain",
          cf._name_present("Northwind Ventures", "northwind.vc",
                           page("Acme Landscaping", "we invest in lawns and ventures")),
          False)
    check("parked page",
          cf._name_present("Cobalt Capital", "cobaltcap.vc",
                           page("Domain for sale", "this venture domain is for sale")),
          False)
    check("real firm, someone else's domain",
          cf._name_present("Emergence Two", "emcap.com", page("Emergence Capital")),
          False)
    # narrow exception: client-rendered shell has no text to match against
    check("JS shell falls back to domain",
          cf._name_present("Sapphire Ventures", "sapphireventures.com",
                           "<html><head><title>Home</title></head><body></body></html>"),
          True)
    check("JS shell, domain doesn't match either",
          cf._name_present("Northwind Ventures", "unrelated.com",
                           "<html><title>Home</title></html>"),
          False)


# ------------------------------------------------------------------- verify()
_SITES = {
    "https://emcap.com": page("Emergence Capital"),
    "https://parked.vc": page("Domain for sale", "this venture domain is for sale"),
    "https://realvc.com": page("Real Ventures"),
    "https://bakery.com": page("Joe's Bakery", "we bake bread"),
    # a real VC whose portfolio only exists client-rendered: homepage verifies,
    # but no portfolio page ever resolves — the hard-gate case
    "https://shellvc.com": page("Shell Ventures"),
}


def _fake_fetch(url: str, timeout: int = 20):
    return _SITES.get(url.rstrip("/"))


cf._browser_fetch = lambda url: None   # never hit the real network


def _fake_resolve(domain: str, **kw):
    if "emcap" in domain:
        return "https://emcap.com/portfolio"
    if "realvc" in domain:
        return "https://realvc.com/portfolio"
    return None


def test_verify() -> None:
    print("\nverify()")
    extract.fetch, extract.resolve_portfolio_url = _fake_fetch, _fake_resolve
    check("good firm -> queued with portfolio url",
          cf.verify("Emergence Capital", "emcap.com"),
          (True, "verified", "https://emcap.com/portfolio"))
    # HARD gate since terminal no-url retirement: a null portfolio candidate is
    # a guaranteed tombstone, so it is rejected here instead of queued
    check("verified but no portfolio page -> rejected",
          cf.verify("Shell Ventures", "shellvc.com"),
          (False, "no portfolio page resolved — client-rendered or "
                  "none published", None))
    check("unreachable homepage -> rejected",
          cf.verify("Ghost Fund", "nosuch.example")[:2],
          (False, "homepage unreachable"))
    check("parked domain -> rejected",
          cf.verify("Cobalt Capital", "parked.vc")[0], False)
    check("not a VC site -> rejected",
          cf.verify("Joe's Bakery", "bakery.com")[1],
          "site doesn't read like a VC firm")
    check("unusable homepage -> rejected",
          cf.verify("Bad Domain", "not a url")[0], False)


# ---------------------------------------------------------------------- find()
_PROPOSALS = [
    {"firm_name": "Accel", "homepage": "accel.com"},                 # already a dataset
    {"firm_name": "Craft Ventures", "homepage": "craftventures.com"},  # on the seed list
    {"firm_name": "Benchmark", "homepage": "benchmark.com"},         # known no-portfolio
    {"firm_name": "Cobalt Capital", "homepage": "parked.vc"},        # fails verification
    {"firm_name": "Emergence Two", "homepage": "emcap.com"},         # wrong domain
    {"firm_name": "Real Ventures", "homepage": "realvc.com"},        # the one good lead
    {"firm_name": "Second Good", "homepage": "emcap.com"},           # would be #2
]


def test_find() -> None:
    print("\nfind()  (limit=1, proposals stubbed)")
    extract.fetch, extract.resolve_portfolio_url = _fake_fetch, _fake_resolve
    cf.propose = lambda exclude, n: _PROPOSALS
    data = pathlib.Path(__file__).resolve().parent.parent / "data"
    # via identity, exactly as find() does — a narrow "*_companies.json"
    # glob silently drops companies.json and hides the Lightspeed bug
    known = [p.name for p in data.glob("*.json") if identity.slug_from_file(p.name)]

    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)          # never touch the real queue file
        added = cf.find(store=None, known_files=known, limit=1)
        entries = json.loads((cf._DATA / cf.FILE).read_text())

    check("exactly one firm added", [a["firm_name"] for a in added], ["Real Ventures"])
    by_name = {e["firm_name"]: e for e in entries}
    check("repo dupe not recorded", "Accel" in by_name, False)
    check("seed-list dupe not recorded", "Craft Ventures" in by_name, False)
    check("no-portfolio firm rejected without a fetch",
          by_name["Benchmark"]["reason"], "hand-verified: publishes no portfolio")
    check("parked domain rejected", by_name["Cobalt Capital"]["status"], "rejected")
    check("wrong-domain firm rejected", by_name["Emergence Two"]["status"], "rejected")
    check("limit stops the loop", "Second Good" in by_name, False)
    check("rejects kept as memory",
          sum(1 for e in entries if e["status"] == "rejected"), 3)


def test_known_firm_dedup() -> None:
    """Regression: the live run proposed Lightspeed, which the repo already has.

    Its dataset is companies.json — the one file predating the
    <slug>_companies.json convention — so it produced no slug, is_known() could
    not see it, and it was missing from the model's exclude list too."""
    print("\nknown-firm dedup  (companies.json / Lightspeed)")
    import identity
    check("legacy filename yields a slug",
          identity.slug_from_file("companies.json"), "lightspeed")
    check("slug maps back to the legacy filename",
          identity.data_file_for("lightspeed"), "companies.json")
    check("normal filenames unaffected",
          (identity.slug_from_file("accel_companies.json"),
           identity.data_file_for("accel")),
          ("accel", "accel_companies.json"))
    check("non-datasets still yield nothing",
          identity.slug_from_file("gen_attempts.json"), None)

    known = ["companies.json", "accel_companies.json"]
    check("full firm name recognised",
          identity.is_known("Lightspeed Venture Partners", known), True)
    check("bare firm name recognised", identity.is_known("Lightspeed", known), True)
    check("genuinely new firm still new",
          identity.is_known("Canaan Partners", known), False)
    check("Lightspeed reaches the model's exclude list",
          "Lightspeed Venture Partners" in cf._excludes(known, []), True)


def test_standalone_default_sees_every_dataset() -> None:
    """Regression: `candidate_finder.py --dry-run` proposed Lightspeed AGAIN.

    identity had learned the companies.json alias, but find()'s own default
    known_files still globbed "*_companies.json" — a pattern that structurally
    cannot match companies.json. The fix was the same lesson as the first time:
    ask identity what a dataset is instead of matching filenames locally."""
    print("\nstandalone default known_files")
    data = pathlib.Path(__file__).resolve().parent.parent / "data"
    narrow = [p.name for p in data.glob("*_companies.json")]
    viaid = [p.name for p in data.glob("*.json") if identity.slug_from_file(p.name)]
    check("the narrow glob really does miss it",
          "companies.json" in narrow, False)
    check("asking identity finds it", "companies.json" in viaid, True)
    check("and only datasets — not gen_attempts.json etc.",
          identity.slug_from_file("gen_attempts.json"), None)
    check("Lightspeed is known once the file is in the list",
          identity.is_known("Lightspeed Venture Partners", viaid), True)

    # what find() actually builds when called with no known_files
    calls: list = []
    cf.propose = lambda exclude, n: calls.append(exclude) or []
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = data                       # read the real data dir…
        try:
            cf.find(store=None, dry_run=True)
        finally:
            cf._DATA = pathlib.Path(tmp)
    check("the model is told about Lightspeed",
          any("Lightspeed" in n for n in (calls[0] if calls else [])), True)


def test_no_portfolio_is_hard_gate() -> None:
    """Regression (LocalGlobe, first live post-fix run): a verified firm with no
    resolvable portfolio page was queued under the soft gate and was dead within
    one run — scrape one-strike, then terminal factory retirement. The night's
    slot bought a tombstone instead of a firm. The gate is now hard, and the
    loop must keep going and queue the NEXT viable proposal the same night."""
    print("\nno-portfolio proposals are rejected, and the night still queues a firm")
    extract.fetch, extract.resolve_portfolio_url = _fake_fetch, _fake_resolve
    cf.propose = lambda exclude, n: [
        {"firm_name": "Shell Ventures", "homepage": "shellvc.com"},   # verifies, no portfolio
        {"firm_name": "Real Ventures", "homepage": "realvc.com"},     # viable
    ]
    data = pathlib.Path(__file__).resolve().parent.parent / "data"
    known = [p.name for p in data.glob("*.json") if identity.slug_from_file(p.name)]
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)
        added = cf.find(store=None, known_files=known, limit=1,
                        gstate={}, sstate={})
        entries = json.loads((cf._DATA / cf.FILE).read_text())
    by_name = {e["firm_name"]: e for e in entries}
    check("the shell firm is rejected, not queued",
          by_name["Shell Ventures"]["status"], "rejected")
    check("…permanently (kept as memory for the exclude list)",
          any("Shell Ventures" in n for n in cf._excludes(known, entries)), True)
    check("the loop moved on and queued the viable firm",
          [a["firm_name"] for a in added], ["Real Ventures"])
    check("every queued entry carries a portfolio url",
          all(e.get("portfolio_url") for e in cf.queued(entries)), True)


def test_unreachable_is_not_permanent() -> None:
    """A site we couldn't reach says nothing about the firm — Atomico,
    Balderton and Point Nine were all reported unreachable in one live run.
    Recording that as a rejection would blacklist three real firms forever."""
    print("\nunreachable sites are not blacklisted")
    extract.fetch, extract.resolve_portfolio_url = _fake_fetch, _fake_resolve
    cf.propose = lambda exclude, n: [
        {"firm_name": "Atomico", "homepage": "atomico.com"},      # unreachable
        {"firm_name": "Real Ventures", "homepage": "realvc.com"},  # fine
    ]
    data = pathlib.Path(__file__).resolve().parent.parent / "data"
    known = [p.name for p in data.glob("*.json") if identity.slug_from_file(p.name)]
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)
        cf.find(store=None, known_files=known, limit=1)
        entries = json.loads((cf._DATA / cf.FILE).read_text())
    names = [e["firm_name"] for e in entries]
    check("the unreachable firm is not recorded at all", "Atomico" in names, False)
    check("so it stays proposable next time",
          any("Atomico" in n for n in cf._excludes(known, entries)), False)
    check("the reachable one is still queued", "Real Ventures" in names, True)
    check("a parked domain IS still blacklisted",
          cf.verify("Cobalt Capital", "parked.vc")[1] != "homepage unreachable", True)


def test_nickname_firms() -> None:
    """Regression: the first live run queued "Andreessen Horowitz" as brand new,
    though a16z_companies.json holds 852 of its companies. The slug is a
    nickname, so neither gate connected the name to the file — is_known()
    slugified to "andreessenhorowitz", and the exclude list carried only the
    display name "a16z"."""
    print("\nfirms whose slug is a nickname")
    import identity as i
    known = ["a16z_companies.json", "usv_companies.json", "companies.json"]
    for name, want in [("Andreessen Horowitz", True), ("a16z", True),
                       ("Union Square Ventures", True), ("usv", True),
                       ("Lightspeed Venture Partners", True),
                       ("Canaan Partners", False), ("Matrix Partners", False)]:
        check(f"is_known({name!r})", i.is_known(name, known), want)
    check("alt names are exposed for the exclude list",
          i.alt_names_for("a16z"), ("Andreessen Horowitz",))
    check("a firm with no nickname returns nothing",
          i.alt_names_for("accel"), ())
    check("the model is told both names",
          all(n in cf._excludes(known, []) for n in ("a16z", "Andreessen Horowitz")),
          True)

    # and the end-to-end consequence: it can no longer be queued
    extract.fetch, extract.resolve_portfolio_url = _fake_fetch, _fake_resolve
    cf.propose = lambda e, n: [{"firm_name": "Andreessen Horowitz",
                                "homepage": "a16z.com"},
                               {"firm_name": "Real Ventures",
                                "homepage": "realvc.com"}]
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)
        added = cf.find(store=None, known_files=known, limit=1,
                        gstate={}, sstate={})
    check("a16z is no longer queued as a new firm",
          [a["firm_name"] for a in added], ["Real Ventures"])


def test_wasteful_paths() -> None:
    """Three ways the finder used to spend something for nothing."""
    print("\nno wasted calls or commits")
    extract.fetch, extract.resolve_portfolio_url = _fake_fetch, _fake_resolve
    data = pathlib.Path(__file__).resolve().parent.parent / "data"
    known = [p.name for p in data.glob("*.json") if identity.slug_from_file(p.name)]

    # 1. a zero limit must not still pay for a proposal
    calls: list = []
    cf.propose = lambda e, n: calls.append(1) or []
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)
        (cf._DATA / cf.FILE).write_text("[]")
        cf.find(store=None, known_files=known, limit=0, gstate={}, sstate={})
    check("limit=0 makes no API call", calls, [])

    # 2. the same firm named twice in one response is handled once
    cf.propose = lambda e, n: [
        {"firm_name": "Benchmark", "homepage": "benchmark.com"},
        {"firm_name": "Benchmark", "homepage": "benchmark.com"},
        {"firm_name": "Ghost Fund", "homepage": "nosuch.example"},
        {"firm_name": "Ghost Fund", "homepage": "nosuch.example"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)
        (cf._DATA / cf.FILE).write_text("[]")
        cf.find(store=None, known_files=known, limit=1, gstate={}, sstate={})
        names = [e["firm_name"] for e in
                 json.loads((cf._DATA / cf.FILE).read_text())]
    check("a repeated proposal is recorded once", names, ["Benchmark"])

    # 3. a run that learns nothing must not commit an identical file
    class _Store:
        def __init__(self): self.commits = 0
        def read_json(self, f): return []
        def commit_json(self, f, p, m): self.commits += 1
    cf.propose = lambda e, n: [{"firm_name": "Ghost Fund",
                                "homepage": "nosuch.example"}]
    st = _Store()
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)
        cf.find(store=st, known_files=known, limit=1, gstate={}, sstate={})
    check("a night that learns nothing makes no commit", st.commits, 0)

    # ...but a night that DOES learn something still commits
    cf.propose = lambda e, n: [{"firm_name": "Real Ventures",
                                "homepage": "realvc.com"}]
    st2 = _Store()
    with tempfile.TemporaryDirectory() as tmp:
        cf._DATA = pathlib.Path(tmp)
        cf.find(store=st2, known_files=known, limit=1, gstate={}, sstate={})
    check("a night that finds a firm does commit", st2.commits, 1)


def test_backlog_excludes_finished_firms() -> None:
    """Regression: the finder must not throttle itself on a graveyard.

    FIND_MAX_OPEN pauses the finder when too many candidates are unprocessed.
    Counting factory-retired and scrape-dead firms as 'unprocessed' meant the
    count only ever rose — at one new firm a night it crossed 25 on night 16
    and the finder stopped for good."""
    print("\nbacklog count excludes finished firms")
    import gen_state
    import identity
    import scrape_state

    known = ["accel_companies.json"]
    names = ["Uncork Capital", "Craft Ventures", "Accel"]
    check("all unfinished firms count", len(cf._pending(names, known, {}, {})), 2)
    check("a firm with a dataset never counts",
          "Accel" in cf._pending(names, known, {}, {}), False)

    g: dict = {}
    for _ in range(gen_state.max_attempts()):
        gen_state.record_failure(g, identity.slugify("Uncork Capital"), "failed")
    check("factory-retired firm drops out",
          cf._pending(names, known, g, {}), ["Craft Ventures"])

    s: dict = {}
    scrape_state.record_failure(s, identity.slugify("Craft Ventures"),
                                "no portfolio page resolved")
    check("scrape-dead firm drops out too", cf._pending(names, known, g, s), [])

    # the shape that actually mattered: 30 nights, one new dead firm each
    live = list(names)
    g2, s2 = {}, {}
    for night in range(30):
        slug = identity.slugify(f"Firm {night}")
        live.append(f"Firm {night}")
        scrape_state.record_failure(s2, slug, "no portfolio page resolved")
        for _ in range(gen_state.max_attempts()):
            gen_state.record_failure(g2, slug, "failed")
    check("30 nights of dead firms don't accumulate",
          len(cf._pending(live, known, g2, s2)), 2)
    check("so the finder never hits FIND_MAX_OPEN",
          len(cf._pending(live, known, g2, s2)) < 25, True)


def test_roster_merge() -> None:
    print("\nroster merge")
    data = pathlib.Path(__file__).resolve().parent.parent / "data"
    # via identity, exactly as find() does — a narrow "*_companies.json"
    # glob silently drops companies.json and hides the Lightspeed bug
    known = [p.name for p in data.glob("*.json") if identity.slug_from_file(p.name)]

    # `before` must be the SEED list's contribution alone. Reading the real
    # queue here mixed in discovered firms (observed: bcapitalgroup), and since
    # the block below swaps that queue for a fake, those firms legitimately
    # disappear and the subset check below failed. The assertion is about
    # candidates.json surviving the merge, so the discovered side is stubbed
    # empty to measure it. This passed only while the finder had queued nothing.
    _real_discovered = roster._DISCOVERED
    try:
        with tempfile.TemporaryDirectory() as tmp0:
            empty = pathlib.Path(tmp0) / "discovered_candidates.json"
            empty.write_text("[]")
            roster._DISCOVERED = empty
            before = {f.slug for f in roster.build(known) if f.kind == "generic"}
    finally:
        roster._DISCOVERED = _real_discovered

    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "discovered_candidates.json"
        fake.write_text(json.dumps([
            {"firm_name": "Sapphire Ventures", "homepage": "sapphireventures.com",
             "portfolio_url": "https://sapphireventures.com/companies",
             "status": "queued"},
            {"firm_name": "Ghost Fund", "homepage": "ghost.invalid",
             "status": "rejected"},
            {"firm_name": "Accel", "homepage": "accel.com", "status": "queued"},
            {"firm_name": "Craft Ventures", "homepage": "craftventures.com",
             "status": "queued"},
        ], indent=2))
        roster._DISCOVERED = fake
        firms = roster.build(known)

    gen = [f for f in firms if f.kind == "generic"]
    slugs = [f.slug for f in gen]
    check("queued firm reaches the roster", "sapphireventures" in slugs, True)
    check("rejected firm does not", "ghostfund" in slugs, False)
    check("firm already in the repo does not", "accel" in slugs, False)
    check("firm on both lists appears once", slugs.count("craftventures"), 1)
    check("seed list still intact", before <= set(slugs), True)
    check("portfolio_url carried through",
          next(f.portfolio_url for f in gen if f.slug == "sapphireventures"),
          "https://sapphireventures.com/companies")


if __name__ == "__main__":
    test_norm_domain()
    test_name_present()
    test_verify()
    test_find()
    test_known_firm_dedup()
    test_standalone_default_sees_every_dataset()
    test_no_portfolio_is_hard_gate()
    test_unreachable_is_not_permanent()
    test_nickname_firms()
    test_wasteful_paths()
    test_backlog_excludes_finished_firms()
    test_roster_merge()
    print(f"\n{'FAILED: ' + str(len(_FAILS)) if _FAILS else 'all tests passed'}")
    for f in _FAILS:
        print("  -", f)
    raise SystemExit(1 if _FAILS else 0)
