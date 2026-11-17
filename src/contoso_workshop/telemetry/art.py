"""Thematic ASCII art for headings only. Plain ASCII, monochrome-safe, no spinners."""

from __future__ import annotations

OIL_RIG = r"""
        |>
       /|\        CONTOSO OIL  //  Document Intelligence Workshop
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
   [o_o]    {title}
   /|_|\
    / \
"""


def banner(kind: str = "rig", title: str = "") -> str:
    if kind == "barrel":
        return BARREL.format(title=title)
    if kind == "robot":
        return ROBOT.format(title=title)
    return OIL_RIG
