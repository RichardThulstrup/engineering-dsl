from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Set
import re

try:
    import pandas as pd  # optional; only used by methods that return DataFrames
except Exception:
    pd = None


# -------------------------- Data structures --------------------------

@dataclass
class Component:
    ref: str
    footprint: str = ""
    value: str = ""
    raw_block: List[str] = field(default_factory=list)


class Netlist:
    """
    Parser & utilities for a .Net file with:
      - component blocks delimited by [ ... ]
      - net blocks delimited by ( ... )
    """
    def __init__(self):
        # net_name -> list of (ref, pin)
        self.nets: Dict[str, List[Tuple[str, str]]] = {}
        # ref -> list of (pin, net_name)
        self.ref_to_pins: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        # ref -> Component
        self.components: Dict[str, Component] = {}

    # ---------- parsing ----------
    @classmethod
    def from_file(cls, path: str | Path) -> "Netlist":
        return cls.from_text(Path(path).read_text(encoding="utf-8", errors="replace"))

    @classmethod
    def from_text(cls, text: str) -> "Netlist":
        lines = text.splitlines()
        nl = cls()
        i = 0

        # components: "[" ... "]"
        while i < len(lines):
            if lines[i].strip() == "[":
                block: List[str] = []
                i += 1
                while i < len(lines) and lines[i].strip() != "]":
                    block.append(lines[i].rstrip("\n"))
                    i += 1
                ref = block[0].strip() if block else ""
                fp = block[1].strip() if len(block) > 1 else ""
                val = ""
                for line in block[2:]:
                    if line.strip():
                        val = line.strip()
                        break
                if ref:
                    nl.components[ref] = Component(ref=ref, footprint=fp, value=val, raw_block=block)
            i += 1

        # nets: "(" ... ")"
        i = 0
        pin_entry = re.compile(r"([A-Za-z0-9_]+)-(\w+)$")
        while i < len(lines):
            if lines[i].strip() == "(":
                block: List[str] = []
                i += 1
                while i < len(lines) and lines[i].strip() != ")":
                    block.append(lines[i].strip())
                    i += 1
                if block:
                    net_name = block[0]
                    conns: List[Tuple[str, str]] = []
                    for ent in block[1:]:
                        m = pin_entry.match(ent)
                        if m:
                            ref, pin = m.group(1), m.group(2)
                            conns.append((ref, pin))
                            nl.ref_to_pins[ref].append((pin, net_name))
                    nl.nets[net_name] = conns
            i += 1

        return nl

    # ---------- helpers ----------
    @staticmethod
    def is_ground_net(name: str) -> bool:
        n = name.upper()
        return any(g in n for g in ["GND", "PGND", "AGND", "DGND", "GROUND", "GNDA", "GNDD"])

    @staticmethod
    def is_capacitor(ref: str, value: str = "") -> bool:
        if ref.upper().startswith("C"):
            return True
        v = (value or "").upper()
        return any(s in v for s in ["F", "UF", "NF", "PF"]) and not any(s in v for s in ["H", "OHM", "Ω"])

    def candidate_rails(self) -> List[str]:
        kws = ["3V", "1V", "5V", "VDD", "VCC", "VIN", "VBAT", "VIO", "AVDD", "DVDD", "VDDA", "VSSA", "VSS"]
        return sorted({n for n in self.nets if any(k in n.upper() for k in kws)})

    def ground_nets(self) -> List[str]:
        return sorted([n for n in self.nets if self.is_ground_net(n)])

    # Optional convenience if you like DataFrames:
    def find_decoupling_caps(self, rail_pattern: str):
        if pd is None:
            raise RuntimeError("pandas not available: install pandas or use the set-based helpers below.")
        pat = rail_pattern.upper()
        rows = []
        for ref, comp in self.components.items():
            if not self.is_capacitor(ref, comp.value):
                continue
            pins = self.ref_to_pins.get(ref, [])
            if not pins:
                continue
            gnet = gpin = rnet = rpin = None
            for pin, net in pins:
                if self.is_ground_net(net):
                    gnet, gpin = net, pin
                if pat in net.upper():
                    rnet, rpin = net, pin
            if gnet and rnet and gpin != rpin:
                rows.append({
                    "ref": ref,
                    "value": comp.value,
                    "footprint": comp.footprint,
                    "rail_net": rnet,
                    "rail_pin": rpin,
                    "ground_net": gnet,
                    "ground_pin": gpin,
                })
        return pd.DataFrame(rows).sort_values(["rail_net", "ref"]) if rows else pd.DataFrame(columns=[
            "ref","value","footprint","rail_net","rail_pin","ground_net","ground_pin"
        ])


# -------------------------- set-based API (module-level) --------------------------

def refs_on_nets_matching(
    nl,
    net_query: str,
    ref_regex: str = r".*",
    match: str = "exact",          # "contains" | "exact" | "regex"
    case_insensitive: bool = True,
) -> Set[str]:
    """
    Return refdes that touch any net matching `net_query`.
    - match="exact"   -> net name must equal net_query
    - match="regex"   -> fullmatch of regex net_query (use ^...$ if you like)
    - match="contains" (default) -> substring match
    `ref_regex` filters refdes (fullmatch), e.g. r'^[Cc]\\d+' for capacitors.
    """
    flags = re.IGNORECASE if case_insensitive else 0
    ref_pat = re.compile(ref_regex, flags)

    if match == "regex":
        net_pat = re.compile(net_query, flags)
        net_ok = lambda name: bool(net_pat.fullmatch(name))
    elif match == "exact":
        q = net_query.lower() if case_insensitive else net_query
        net_ok = (lambda name: name.lower() == q) if case_insensitive else (lambda name: name == q)
    else:  # "contains"
        q = net_query.lower() if case_insensitive else net_query
        net_ok = (lambda name: q in name.lower()) if case_insensitive else (lambda name: q in name)

    refs: Set[str] = set()
    for net_name, conns in nl.nets.items():
        if net_ok(net_name):
            for ref, _pin in conns:
                if ref_pat.fullmatch(ref):
                    refs.add(ref)
    return refs



def intersect_refs_between(
    nl,
    net_a_query: str,
    net_b_query: str,
    ref_regex: str = r".*",
    match: str = "exact",          # "contains" | "exact" | "regex"
    case_insensitive: bool = True,
) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Intersection of refdes that touch *both* net queries.
    Returns (common, setA, setB).
    """
    A = refs_on_nets_matching(nl, net_a_query, ref_regex, match, case_insensitive)
    B = refs_on_nets_matching(nl, net_b_query, ref_regex, match, case_insensitive)
    return (A & B, A, B)

def details_for_refs(nl: Netlist, refs: Set[str]) -> List[dict]:
    """Return simple details (value + nets) for a set of refdes."""
    rows: List[dict] = []
    for ref in sorted(refs):
        comp = nl.components.get(ref)
        value = comp.value if comp else ""
        nets = sorted({net for (pin, net) in nl.ref_to_pins.get(ref, [])})
        rows.append({"ref": ref, "value": value, "nets": nets})
    return rows


__all__ = [
    "Component",
    "Netlist",
    "refs_on_nets_matching",
    "intersect_refs_between",
    "details_for_refs",
]
