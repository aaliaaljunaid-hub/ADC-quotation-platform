"""Plot the DXF paper-space sheets to a multi-page PDF (A3 landscape)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from .dxf_writer import PAPER_H, PAPER_W  # noqa: E402

MM_PER_INCH = 25.4


def write_pdf(doc, layout_names: list[str], path: str) -> None:
    config = Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.COLOR,
        min_lineweight=0.1,
        lineweight_scaling=1.0,
    )
    with PdfPages(path) as pdf:
        for name in layout_names:
            layout = doc.layouts.get(name)
            fig = plt.figure(figsize=(PAPER_W / MM_PER_INCH, PAPER_H / MM_PER_INCH))
            ax = fig.add_axes((0, 0, 1, 1))
            ctx = RenderContext(doc)
            ctx.set_current_layout(layout)
            Frontend(ctx, MatplotlibBackend(ax), config=config).draw_layout(layout, finalize=True)
            ax.set_xlim(0, PAPER_W)
            ax.set_ylim(0, PAPER_H)
            ax.set_aspect("equal", adjustable="box")
            ax.axis("off")
            pdf.savefig(fig)
            plt.close(fig)


def write_png(doc, layout_name: str, path: str, dpi: int = 110) -> None:
    """Single-sheet preview image for the web UI."""
    layout = doc.layouts.get(layout_name)
    fig = plt.figure(figsize=(PAPER_W / MM_PER_INCH, PAPER_H / MM_PER_INCH))
    ax = fig.add_axes((0, 0, 1, 1))
    ctx = RenderContext(doc)
    ctx.set_current_layout(layout)
    config = Configuration(background_policy=BackgroundPolicy.WHITE)
    Frontend(ctx, MatplotlibBackend(ax), config=config).draw_layout(layout, finalize=True)
    ax.set_xlim(0, PAPER_W)
    ax.set_ylim(0, PAPER_H)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
