"""Storm-cell tracking: identify convective cells in a reflectivity grid and
(Slice 28b) associate them across frames to estimate motion.

This is *estimation*, not the provably-correct radar render. Cell identification
and cross-frame association are heuristics ported from the documented TITAN/SCIT
method — they are framed in-UI as estimated cell motion, never a nowcast, and
the not-for-life-safety constraint applies (ADR pending; see ROADMAP Slice 28).
"""
