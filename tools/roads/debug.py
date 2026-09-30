"""Pictures of the road network for the eye: plan views of any spot, a gallery of junctions, height profiles of links."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection, PolyCollection

from .metrics import ribbon_polygon

ASPHALT, DIRT, JUNCTION, BAD = "#8a8a90", "#b89a70", "#6f6f78", "#e04030"


def plan(ax, network, centre, radius, raw=None, labels=False):
    """Plan view of the network within `radius` of `centre`."""
    cx, cz = centre

    def near(xy):
        return len(xy) > 0 and np.abs(xy[:, 0] - cx).min() < radius and np.abs(xy[:, 1] - cz).min() < radius

    ribbons, colours, centrelines = [], [], []
    for k, link in enumerate(network.links):
        if not near(link.xy):
            continue
        outline = ribbon_polygon(link)
        if outline is not None:
            ribbons.append(np.asarray(outline.exterior.coords))
            colours.append(DIRT if network.edges[link.chain[0][0]].dirt else ASPHALT)
        centrelines.append(link.xy)
        if labels:
            mid = link.xy[len(link.xy) // 2]
            ax.text(mid[0], mid[1], f"L{k}", fontsize=6, color="navy", clip_on=True)
    ax.add_collection(PolyCollection(ribbons, facecolors=colours, edgecolors="none"))
    for k, junction in enumerate(network.junctions):
        if junction.vertices is None or not near(junction.vertices):
            continue
        ax.add_collection(PolyCollection(junction.vertices[junction.triangles], facecolors=JUNCTION if junction.valid else BAD, edgecolors="#55555a", linewidths=0.2))
        edges = junction.vertices[junction.boundary[:, :2]]
        ax.add_collection(LineCollection(edges[junction.boundary[:, 2] == 0], colors="k", linewidths=0.5))
        ax.add_collection(LineCollection(edges[junction.boundary[:, 2] == 1], colors="white", linewidths=0.9))
        if labels:
            ax.text(junction.centre[0], junction.centre[1], f"J{k}", fontsize=7, color="darkred", clip_on=True)
    if raw is not None:
        ax.add_collection(LineCollection([xy for xy in raw if near(xy)], colors="red", linewidths=0.5, linestyles="dotted"))
    ax.add_collection(LineCollection(centrelines, colors="gold", linewidths=0.5))
    ax.set_xlim(cx - radius, cx + radius)
    ax.set_ylim(cz - radius, cz + radius)
    ax.set_aspect("equal")
    ax.set_facecolor("#9fbf8f")


def save_plan(network, centre, radius, path, raw=None, labels=True, size=11):
    fig, ax = plt.subplots(figsize=(size, size))
    plan(ax, network, centre, radius, raw, labels)
    ax.set_title(f"({centre[0]:.0f}, {centre[1]:.0f}) +- {radius:.0f} m")
    fig.savefig(path, dpi=90, bbox_inches="tight")
    plt.close(fig)


def gallery(network, junction_ids, path, radius=22.0, columns=6):
    """Small plan views of many junctions on one sheet."""
    rows = max((len(junction_ids) + columns - 1) // columns, 1)
    fig, axes = plt.subplots(rows, columns, figsize=(3.2 * columns, 3.2 * rows), squeeze=False)
    for ax in axes.ravel():
        ax.set_xticks([])
        ax.set_yticks([])
    for ax, k in zip(axes.ravel(), junction_ids):
        junction = network.junctions[k]
        plan(ax, network, tuple(junction.centre), radius)
        ax.set_title(f"J{k} ({junction.centre[0]:.0f}, {junction.centre[1]:.0f})", fontsize=8)
    fig.savefig(path, dpi=80, bbox_inches="tight")
    plt.close(fig)


def profile(network, link_ids, path):
    """Ground samples against the solved height profile, grade and vertical curvature, one row per link."""
    fig, axes = plt.subplots(len(link_ids), 3, figsize=(18, 3.2 * len(link_ids)), squeeze=False)
    for row, k in zip(axes, link_ids):
        link = network.links[k]
        grade = np.gradient(link.z, link.s)
        row[0].plot(link.s, link.ground, ".", ms=2, color="#b06030", label="ground")
        row[0].plot(link.s, link.z, "-", lw=1.2, color="k", label="road")
        row[0].set_title(f"L{k} height (m)", fontsize=9)
        row[0].legend(fontsize=7)
        row[1].plot(link.s, 100 * np.gradient(link.ground, link.s), lw=0.5, color="#b06030")
        row[1].plot(link.s, 100 * grade, lw=1.2, color="k")
        row[1].set_title("grade (%)", fontsize=9)
        row[2].plot(link.s, 1000 * np.gradient(grade, link.s), lw=1.0, color="k")
        row[2].set_title("vertical curvature (1/km): crest < 0 < sag", fontsize=9)
    fig.savefig(path, dpi=80, bbox_inches="tight")
    plt.close(fig)


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    return Path(path)
