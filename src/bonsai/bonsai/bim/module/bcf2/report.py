# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2026 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.
#
# This file was generated with the assistance of an AI coding tool.

"""BCF Project 2 report: every topic of the loaded BCF as one self-contained HTML file -
an overview table, then one page per topic with its viewpoint snapshots and BCF fields.
Images are embedded, so it's a single file to send; the print styles make the browser's
"Print > Save as PDF" a clean PDF (overview first, each topic on its own page)."""

import base64
import datetime
import html
import os
from pathlib import Path

import bpy
from bpy_extras.io_utils import ExportHelper

import bonsai.tool as tool
from bonsai.bim.module.bcf2 import bcfstore, topic_columns

FIELDS = (
    ("type", "Type"),
    ("status", "Status"),
    ("priority", "Priority"),
    ("stage", "Stage"),
    ("assigned_to", "Assigned To"),
    ("due_date", "Due Date"),
    ("creation_date", "Created"),
    ("creation_author", "Created By"),
    ("modified_date", "Modified"),
    ("modified_author", "Modified By"),
)
OVERVIEW = ("status", "priority", "type", "assigned_to", "due_date", "modified_date")
DATES = {"due_date", "creation_date", "modified_date"}

CSS = """
:root { --ink:#1d2329; --muted:#5f6b76; --line:#d5dbe1; --head:#f1f4f7; }
* { box-sizing:border-box; }
body { font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; color:var(--ink);
       margin:0 auto; max-width:1100px; padding:24px 16px; font-size:14px; line-height:1.45; }
h1 { font-size:24px; margin:0 0 4px; } h2 { font-size:19px; margin:0 0 12px; }
.meta { color:var(--muted); margin-bottom:20px; }
table { border-collapse:collapse; width:100%; }
th, td { border:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { background:var(--head); font-weight:600; }
.overview td:first-child, .overview th:first-child { width:3em; text-align:right; }
.overview a { color:inherit; }
.topic { border-top:3px solid var(--ink); margin-top:36px; padding-top:16px; }
.num { color:var(--muted); font-weight:400; margin-right:6px; }
.snaps img { display:block; max-width:100%; max-height:560px; margin:0 auto 10px; border:1px solid var(--line); }
.fields th { width:9.5em; }
.description { white-space:pre-wrap; border:1px solid var(--line); padding:8px 10px; margin:12px 0; }
.comments { margin-top:12px; } .comment { border-left:3px solid var(--line); padding:4px 10px; margin:6px 0; }
.comment .by { color:var(--muted); font-size:12px; } .comment .text { white-space:pre-wrap; }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; background:#e3e7eb; }
.badge.open { background:#f8d7d7; } .badge.progress { background:#fde7c8; } .badge.done { background:#d6efd9; }
.none { color:var(--muted); font-style:italic; }
@page { size:A4; margin:12mm; }
@media print {
  body { max-width:none; padding:0; font-size:11pt; }
  .topic { page-break-before:always; border-top:none; margin-top:0; padding-top:0; }
  .snaps img { max-height:150mm; }
  tr, .comment { page-break-inside:avoid; }
  .overview a { text-decoration:none; }
}
"""


def _esc(value) -> str:
    return html.escape(str(value or ""))


def _value(field: str, value: str) -> str:
    if field in DATES and value:
        return value[:16].replace("T", " ")  # ISO date and time, no seconds
    return value


def _badge(status: str) -> str:
    s = status.lower()
    kind = ""
    if any(w in s for w in ("closed", "resolved", "done", "solved", "åtgärda", "stängd", "klar")):
        kind = "done"
    elif any(w in s for w in ("progress", "pågå", "active")):
        kind = "progress"
    elif any(w in s for w in ("open", "öppen", "new")):
        kind = "open"
    return f'<span class="badge {kind}">{_esc(status)}</span>' if status else ""


def _ordered(topics, columns) -> list:
    """Topics in the list's current sort order (same rules as topic_columns.filter_items)."""
    order = list(range(len(topics)))
    for field, descending in reversed(topic_columns.sort_levels(columns)):
        order.sort(key=lambda i: getattr(topics[i], field).lower(), reverse=descending)
        order = [i for i in order if getattr(topics[i], field)] + [i for i in order if not getattr(topics[i], field)]
    return [topics[i] for i in order]


def _snapshots(bcfxml, guid: str) -> list[bytes]:
    """A topic's viewpoint snapshots, read straight from the .bcf (no viewpoint parsing) - or from
    memory for viewpoints added this session and not saved yet."""
    topic = bcfxml.topics.get(guid) if bcfxml else None
    if topic is None:
        return []
    loaded = getattr(topic, "_viewpoints", None) or {}
    topic_dir = getattr(topic, "_topic_dir", None)
    images = []
    for viewpoint in tool.Bcf2.get_topic_viewpoints(topic) or []:
        data = None
        if viewpoint.viewpoint in loaded:
            data = loaded[viewpoint.viewpoint].snapshot
        elif viewpoint.snapshot and topic_dir is not None:
            path = topic_dir.joinpath(viewpoint.snapshot)
            if path.exists():
                data = path.read_bytes()
        if data:
            images.append(data)
    return images


def _img(data: bytes) -> str:
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return f'<img src="data:{mime};base64,{base64.b64encode(data).decode("ascii")}" alt="Viewpoint snapshot">'


def build_report(props, bcfxml, bcf_file: str) -> tuple[str, int, int]:
    """Returns (html, topic count, image count)."""
    topics = _ordered(list(props.topics), props.topic_columns)
    titles = {t.name: t.title for t in props.topics}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    title = props.name or Path(bcf_file).stem or "BCF report"
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{_esc(title)} - BCF report</title><style>{CSS}</style></head><body>",
        f"<h1>{_esc(title)}</h1>",
        f"<div class='meta'>{len(topics)} topics · {_esc(os.path.basename(bcf_file) or 'unsaved BCF')} · exported {now}</div>",
        "<table class='overview'><tr><th>#</th><th>Title</th>",
        "".join(f"<th>{_esc(dict(FIELDS)[f])}</th>" for f in OVERVIEW),
        "</tr>",
    ]
    for n, topic in enumerate(topics, start=1):
        cells = "".join(
            f"<td>{_badge(topic.status) if f == 'status' else _esc(_value(f, getattr(topic, f)))}</td>"
            for f in OVERVIEW
        )
        parts.append(
            f"<tr><td>{n}</td><td><a href='#t{n}'>{_esc(topic.title) or '&lt;no title&gt;'}</a></td>{cells}</tr>"
        )
    parts.append("</table>")

    images = 0
    for n, topic in enumerate(topics, start=1):
        parts.append(f"<section class='topic' id='t{n}'>")
        parts.append(f"<h2><span class='num'>{n}.</span>{_esc(topic.title) or '&lt;no title&gt;'}</h2>")
        snaps = _snapshots(bcfxml, topic.name)
        images += len(snaps)
        parts.append(
            "<div class='snaps'>" + "".join(_img(s) for s in snaps) + "</div>"
            if snaps
            else "<p class='none'>No viewpoint snapshot</p>"
        )
        rows = [
            f"<tr><th>{label}</th><td>{_badge(topic.status) if field == 'status' else _esc(_value(field, getattr(topic, field)))}</td></tr>"
            for field, label in FIELDS
            if getattr(topic, field)
        ]
        if labels := [l.name for l in topic.labels if l.name]:
            rows.append(f"<tr><th>Labels</th><td>{_esc(', '.join(labels))}</td></tr>")
        if links := [l.name for l in topic.reference_links if l.name]:
            rows.append("<tr><th>Links</th><td>" + "<br>".join(_esc(l) for l in links) + "</td></tr>")
        if related := [titles.get(r.name, r.name) for r in topic.related_topics]:
            rows.append(f"<tr><th>Related</th><td>{_esc(', '.join(related))}</td></tr>")
        rows.append(f"<tr><th>GUID</th><td>{_esc(topic.name)}</td></tr>")
        parts.append("<table class='fields'>" + "".join(rows) + "</table>")
        if topic.description:
            parts.append(f"<div class='description'>{_esc(topic.description)}</div>")
        comments = [c for c in topic.comments if c.comment]
        if comments:
            parts.append("<div class='comments'><strong>Comments</strong>")
            for c in sorted(comments, key=lambda c: c.date):
                by = " · ".join(x for x in (_esc(c.author), _esc(_value("creation_date", c.date))) if x)
                parts.append(
                    f"<div class='comment'><div class='by'>{by}</div><div class='text'>{_esc(c.comment)}</div></div>"
                )
            parts.append("</div>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts), len(topics), images


class ExportBcf2HtmlReport(bpy.types.Operator, ExportHelper):
    """Export every topic of this BCF as one HTML report: overview table, then one page per topic with
    its snapshots and fields. Open it in a browser; Print > Save as PDF gives a PDF"""

    bl_idname = "bcf2.export_html_report"
    bl_label = "Export HTML Report"
    bl_options = {"REGISTER"}
    filename_ext = ".html"
    filter_glob: bpy.props.StringProperty(default="*.html", options={"HIDDEN"})
    open_after: bpy.props.BoolProperty(name="Open in Browser", default=True)

    @classmethod
    def poll(cls, context):
        if not bcfstore.Bcf2Store.get_bcfxml() or not len(tool.Bcf2.get_bcf_props().topics):
            cls.poll_message_set("Load a BCF project with topics first.")
            return False
        return True

    def invoke(self, context, event):
        props = tool.Bcf2.get_bcf_props()
        bcf_file = bpy.path.abspath(props.bcf_file) if props.bcf_file else ""
        folder = os.path.dirname(bcf_file) or bpy.path.abspath("//")
        stem = Path(bcf_file).stem if bcf_file else (props.name or "BCF")
        self.filepath = os.path.join(folder, f"{stem} report.html")
        return super().invoke(context, event)

    def execute(self, context):
        props = tool.Bcf2.get_bcf_props()
        bcf_file = bpy.path.abspath(props.bcf_file) if props.bcf_file else ""
        report, topics, images = build_report(props, bcfstore.Bcf2Store.get_bcfxml(), bcf_file)
        path = bpy.path.ensure_ext(self.filepath, ".html")
        try:
            Path(path).write_text(report, encoding="utf-8")
        except OSError as e:
            self.report({"ERROR"}, f"Couldn't write the report: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"BCF report: {topics} topics, {images} images -> {os.path.basename(path)}")
        if self.open_after:
            bpy.ops.wm.url_open(url=Path(path).resolve().as_uri())
        return {"FINISHED"}
