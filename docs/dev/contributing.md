# Contributing

Contributions are welcome. backscatter is a small, deliberately readable project — the
goal is to keep it that way.

(There's also a short [`CONTRIBUTING.md`](https://github.com/kbennett2000/backscatter/blob/main/CONTRIBUTING.md)
in the repo root; this page is the fuller version.)

## Get set up

See [Run it locally](local.md) for the clone-and-go steps, and
[Running the tests](testing.md) for the checks. In short: `uv sync`, then keep `pytest` /
`ruff` / `mypy` / `node --test web/gaps.test.js` green.

## How changes are made here

- **Small, reviewable vertical slices.** One coherent change per branch/PR, working end
  to end, with tests. Not a giant pile of half-finished pieces. The
  [Roadmap](../ROADMAP.md) is the running history of these slices.
- **Decisions get an ADR.** Any real architectural choice gets a short record in
  [`docs/decisions/`](https://github.com/kbennett2000/backscatter/tree/main/docs/decisions)
  — context, the decision, consequences, and the alternatives considered. See the
  [Design decisions](decisions.md) index.
- **Match the surrounding code.** Naming, comment density, and idiom should read like the
  code already there.

## The one rule: prove the rendering

This is the trap in radar software, so it gets its own rule:

!!! warning "A wrong radar image can look completely plausible"
    A bad projection, a flipped axis, an off-by-one in the gate geometry, or a wrong
    color-table mapping all produce an image that looks fine but is **wrong**. You can't
    eyeball your way to correctness.

So anything touching **geometry** (range/azimuth → lat/lon), **reprojection**, or **color
mapping** gets:

1. **Value-based tests** against known-correct numbers, and
2. **A visual sanity check** against a reference (RadarScope, the NWS site, etc.) for the
   same timestamp.

Never merge a rendering change on "it produced an image." Produce the *right* image and
prove it.

## Hard constraints (please don't break these)

- **No paid anything.** No API keys, no paid data feeds, nothing needing a credit card.
  All radar data is NOAA's free public S3. If something looks like it needs a paid
  service, there's almost always a free path — flag it.
- **Self-hosted / LAN-first.** It runs on a home machine; S3 access is anonymous. No
  cloud account required.
- **Not life-safety.** backscatter is a hobby tool. Keep that framing; don't add features
  that imply official warning capability.

## Sending a change

1. Fork and branch.
2. Make the change with its tests; keep the checks green.
3. If you made an architectural decision, add the ADR.
4. Open a PR describing what to look at and why. Keep the diff small and coherent.

Found a bug or have an idea? Open an
[issue](https://github.com/kbennett2000/backscatter/issues) first — it's the easiest way
to start a conversation.
