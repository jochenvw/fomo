"""Thematic ASCII mascots for human-readable headings; titles may include emoji."""

from __future__ import annotations

OIL_RIG = r"""
        |>
       /|\        FOMO  //  Metadata-Agent Workshop
      / | \
     /__|__\      Simple RAG  ->  Metadata Miner  ->  Smart RAG
    |___|___|
~~~~|~~~|~~~|~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""

BARREL = r"""
   .-----.
  (_______)   {title}
  |  OIL  |
  |_______|
"""

ROBOT = r"""
   [o_o]    F O M O
   /|_|\    Foundry Organizes Metadata.
    / \     Occasionally.
            {title}
"""


def banner(kind: str = "rig", title: str = "") -> str:
    if kind == "barrel":
        return BARREL.format(title=title)
    if kind == "robot":
        return ROBOT.format(title=title)
    return OIL_RIG
