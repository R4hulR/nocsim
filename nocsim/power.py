"""Area, energy and timing model built from DSENT characterisation data.

``tools/dsent_main/characterize.py`` runs DSENT (Sun et al., NOCS 2012) for a
5-port router (128-bit flits, 3 VCs x 8-flit buffers) and for repeated
global-layer links of increasing length, at several clock frequencies. This
module turns those numbers into per-topology figures:

* **Area:** routers plus link wiring. A link is two 128-bit channels (one
  per direction) on the global metal layer, so its track area is
  2 x 128 x wire pitch x length, plus DSENT's repeater area.
* **Energy:** dynamic energy per flit through a router (buffer write + read,
  crossbar, switch arbitration), per flit per mm of link, clock-tree energy
  per router per cycle, and leakage of routers and link repeaters over the
  run time. The simulator supplies the activity counts.
* **Timing:** DSENT sizes repeaters to fit a wire into one clock period; the
  longest wire that still fits is the *reach* at that frequency. Two regimes
  follow, both used in the paper:
    - *global clock* (Kite, NetSmith): the network runs at the highest
      frequency at which its longest link fits in one cycle;
    - *pipelined links*: the clock is fixed and a link of length L takes
      ceil(L / reach) cycles (``SimConfig.link_latency="wire"``).

Each router is modelled with its actual port count (network links + local
port): a mesh's corner and edge routers have 3 and 4 ports, every router of a
radix-4 design has 5. DSENT is run for each port count.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .topology import Topology

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "results" / "dsent_characterization.json"


@dataclass(frozen=True)
class TechModel:
    """DSENT-derived constants for one technology node and clock frequency."""

    tech_nm: int
    freq_hz: float
    tile_mm: float  # router-to-router pitch on the floorplan
    e_router_flit: float  # J per flit switched through a router
    e_router_clock: float  # J per router per cycle (clock tree)
    p_router_leak: float  # W per router
    a_router_mm2: float
    e_link_flit_mm: float  # J per flit per mm of link
    wire_pitch_mm: float  # global-layer track pitch
    flit_bits: int
    reach_by_freq: tuple  # ((freq_hz, reach_mm), ...) over all characterised frequencies
    link_table: tuple  # ((length_mm, leak_W, repeater_area_mm2), ...) at freq_hz, one channel
    router_by_ports: tuple = ()  # ((ports, e_flit_J, e_clock_J, leak_W, area_mm2), ...)

    # ---- construction ------------------------------------------------------
    @classmethod
    def from_dsent(cls, path: Path | str = DEFAULT_DATA, tech_nm: int = 22, freq_hz: float = 2e9,
                   tile_mm: float = 1.5, flit_bits: int = 128) -> "TechModel":
        data = json.loads(Path(path).read_text())
        entry = data[f"{tech_nm}nm_{freq_hz / 1e9:g}GHz"]
        r = entry["router"]
        lengths = np.array(sorted(float(k) for k in entry["link_by_length_mm"]))
        rows = [entry["link_by_length_mm"][_key(entry, L)] for L in lengths]
        # Dynamic energy grows almost exactly linearly with length; fit it
        # over the lower half of the feasible range, away from the repeater
        # up-sizing DSENT does close to the one-cycle limit.
        half = lengths <= max(lengths) / 2
        e_slope = np.polyfit(lengths[half], [x["e_send"] for x, h in zip(rows, half) if h], 1)[0]
        table = tuple((float(L), x["p_leak"], x["area"] * 1e6) for L, x in zip(lengths, rows))
        reach = tuple(sorted((v["freq_hz"], v["reach_mm"]) for v in data.values() if v["tech_nm"] == tech_nm))
        by_ports = tuple(sorted(
            (int(k), x["e_write"] + x["e_read"] + x["e_xbar"] + x["e_sa"], x["e_clock"], x["p_leak"], x["area"] * 1e6)
            for k, x in entry.get("router_by_ports", {}).items()))
        return cls(tech_nm=tech_nm, freq_hz=freq_hz, tile_mm=tile_mm,
                   e_router_flit=r["e_write"] + r["e_read"] + r["e_xbar"] + r["e_sa"],
                   e_router_clock=r["e_clock"], p_router_leak=r["p_leak"], a_router_mm2=r["area"] * 1e6,
                   e_link_flit_mm=float(e_slope),
                   wire_pitch_mm=entry["global_wire_pitch_m"] * 1e3, flit_bits=flit_bits,
                   reach_by_freq=reach, link_table=table, router_by_ports=by_ports)

    # ---- timing -------------------------------------------------------------
    def reach_mm(self, freq_hz: float | None = None) -> float:
        """Longest wire that fits in one cycle at ``freq_hz``.

        Repeated-wire delay is close to linear in length, so reach scales as
        1/f; interpolate in the period between characterised frequencies.
        """
        f = freq_hz or self.freq_hz
        pts = sorted((1.0 / fr, re) for fr, re in self.reach_by_freq)  # by increasing period
        return float(np.interp(1.0 / f, [p for p, _ in pts], [r for _, r in pts]))

    def reach_tiles(self, freq_hz: float | None = None) -> float:
        """One-cycle reach in tile pitches, for ``SimConfig.wire_reach``."""
        return self.reach_mm(freq_hz) / self.tile_mm

    def max_frequency(self, topo: Topology) -> float:
        """Global-clock regime: highest frequency at which the longest link fits in a cycle."""
        need = topo.max_link_length * self.tile_mm
        freqs = sorted(f for f, _ in self.reach_by_freq)
        best = freqs[0]
        for f in np.linspace(freqs[0], freqs[-1], 301):
            if self.reach_mm(f) >= need:
                best = float(f)
        return best

    # ---- routers ---------------------------------------------------------------
    def router(self, ports: int) -> tuple[float, float, float, float]:
        """(J per flit, J per clock cycle, leakage W, area mm2) of a router with ``ports`` ports."""
        for p, e, c, lk, a in self.router_by_ports:
            if p == ports:
                return e, c, lk, a
        return self.e_router_flit, self.e_router_clock, self.p_router_leak, self.a_router_mm2

    def _ports(self, topo: Topology) -> list[int]:
        return [d + 1 for d in topo.degrees]  # network links + local port

    # ---- per-link static properties -----------------------------------------
    def channel_static(self, length_mm: float) -> tuple[float, float]:
        """(leakage W, repeater area mm2) of one channel of a link.

        Links longer than the one-cycle reach are pipelined: split into equal
        segments that each fit in a cycle (a flop per segment is ignored).
        """
        lengths = [L for L, _, _ in self.link_table]
        segs = max(1, math.ceil(length_mm / max(lengths)))
        seg = length_mm / segs
        leak = float(np.interp(seg, lengths, [x for _, x, _ in self.link_table]))
        area = float(np.interp(seg, lengths, [x for _, _, x in self.link_table]))
        return segs * leak, segs * area

    def links_static(self, topo: Topology) -> tuple[float, float]:
        """Total link leakage (W) and repeater area (mm2), both directions of every link."""
        leak = area = 0.0
        for u, v in topo.edges:
            lk, ar = self.channel_static(topo.link_length(u, v) * self.tile_mm)
            leak, area = leak + 2 * lk, area + 2 * ar
        return leak, area

    # ---- area ---------------------------------------------------------------
    def area(self, topo: Topology) -> dict:
        routers = sum(self.router(p)[3] for p in self._ports(topo))
        channels_mm = 2 * topo.total_wire_length * self.tile_mm  # two directions per link
        tracks = channels_mm * self.flit_bits * self.wire_pitch_mm
        repeaters = self.links_static(topo)[1]
        return {"router_mm2": routers, "wire_track_mm2": tracks, "repeater_mm2": repeaters,
                "total_mm2": routers + tracks + repeaters}

    # ---- energy -------------------------------------------------------------
    def energy(self, topo: Topology, router_flits: int, link_flit_tiles: int, cycles: int,
               freq_hz: float | None = None, per_router_flits: list | None = None) -> dict:
        """Network energy (J) for a run, from the simulator's activity counts.

        With ``per_router_flits`` each router's flits are charged at its own
        port count's energy; otherwise at the 5-port value.
        """
        f = freq_hz or self.freq_hz
        seconds = cycles / f
        ports = self._ports(topo)
        if per_router_flits is not None:
            router_dyn = sum(fl * self.router(p)[0] for fl, p in zip(per_router_flits, ports))
        else:
            router_dyn = router_flits * self.e_router_flit
        link_dyn = link_flit_tiles * self.tile_mm * self.e_link_flit_mm
        clock = cycles * sum(self.router(p)[1] for p in ports)
        leak = (sum(self.router(p)[2] for p in ports) + self.links_static(topo)[0]) * seconds
        return {"router_dynamic_J": router_dyn, "link_dynamic_J": link_dyn, "clock_J": clock,
                "leakage_J": leak, "total_J": router_dyn + link_dyn + clock + leak, "seconds": seconds}

    def energy_per_packet(self, avg_hops: float, wire_per_packet: float, flits: int = 1) -> float:
        """Dynamic energy (J) of one packet: hops+1 router traversals and its wire."""
        return flits * ((avg_hops + 1) * self.e_router_flit + wire_per_packet * self.tile_mm * self.e_link_flit_mm)


def _key(entry: dict, length: float) -> str:
    """JSON keys are the lengths as written by characterize.py (e.g. '1.0' or '1')."""
    for k in entry["link_by_length_mm"]:
        if math.isclose(float(k), length):
            return k
    raise KeyError(length)
