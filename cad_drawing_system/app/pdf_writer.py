"""Plot the DXF paper-space sheets to a multi-page PDF (A3 landscape)."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from .dxf_writer import LOGO_FILE, PAPER_H, PAPER_W, logo_rect  # noqa: E402

MM_PER_INCH = 25.4
CONFIG = Configuration(
    background_policy=BackgroundPolicy.WHITE,
    color_policy=ColorPolicy.COLOR,
    min_lineweight=0.1,
    lineweight_scaling=1.0,
)


def _render(doc, layout_name: str):
    layout = doc.layouts.get(layout_name)
    fig = plt.figure(figsize=(PAPER_W / MM_PER_INCH, PAPER_H / MM_PER_INCH))
    ax = fig.add_axes((0, 0, 1, 1))
    ctx = RenderContext(doc)
    ctx.set_current_layout(layout)
    # The logo is an external IMAGE reference in the DXF; plot it from the asset directly.
    Frontend(ctx, MatplotlibBackend(ax), config=CONFIG).draw_layout(
        layout, finalize=True, filter_func=lambda e: e.dxftype() != "IMAGE")
    if LOGO_FILE.exists() and any(e.dxftype() == "IMAGE" for e in layout):
        x0, y0, x1, y1 = logo_rect()
        ax.imshow(mpimg.imread(LOGO_FILE), extent=(x0, x1, y0, y1), interpolation="antialiased", zorder=10)
    ax.set_xlim(0, PAPER_W)
    ax.set_ylim(0, PAPER_H)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    return fig


def write_pdf(doc, layout_names: list[str], path: str) -> None:
    with PdfPages(path) as pdf:
        for name in layout_names:
            fig = _render(doc, name)
            pdf.savefig(fig)
            plt.close(fig)


def write_png(doc, layout_name: str, path: str, dpi: int = 110) -> None:
    """Single-sheet preview image for the web UI."""
    fig = _render(doc, layout_name)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
