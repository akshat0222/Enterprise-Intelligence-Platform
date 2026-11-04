from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.agent.registry import ToolContext, ToolError, ToolResult, register_tool
from app.agent.schemas import Report, render_report
from app.prompts import REPORT_SYSTEM_V1
from app.rag.sources import format_sources


class ReportArgs(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    format: Literal["markdown", "json"] = "markdown"
    sections: list[str] = Field([], description="Optional extra section topics to cover")

    @field_validator("format", mode="before")
    @classmethod
    def _lenient_format(cls, value):
        """Models often send 'Markdown', 'md' or 'text'; anything that is not 'json' is markdown."""
        return "json" if str(value).strip().lower() == "json" else "markdown"


@register_tool(
    "generate_report",
    ReportArgs,
    "Turn the document_analysis result into a structured report (summary, findings with evidence, recommendations, "
    "missing information). The analysis is passed automatically.",
    timeout_s=60,
)
def generate_report(args: ReportArgs, t: ToolContext) -> ToolResult:
    analysis = t.state.get("analysis")
    if analysis is None:
        raise ToolError("no analysis available; run document_analysis first")

    used = {e for f in analysis.findings for e in f.evidence}
    sources = [s for s in t.state["sources"] if s.id in used] or t.state["sources"][:10]
    prompt = (
        f"Report title: {args.title}\nExtra sections: {args.sections or 'none'}\n"
        f"User request: {t.state.get('question', '')}\n\n"
        f"<analysis>\n{analysis.model_dump_json()}\n</analysis>\n\n<sources>\n{format_sources(sources, 800)}\n</sources>"
    )
    result = t.gateway.generate(
        "report", REPORT_SYSTEM_V1, [{"role": "user", "content": prompt}], Report, t.ctx, 3500
    )
    report: Report = result.parsed
    valid = {s.id for s in t.state["sources"]}
    for section in report.findings:
        section.evidence = [e for e in section.evidence if e in valid]

    markdown = render_report(report)
    summary = {
        "title": report.title,
        "format": args.format,
        "sections": len(report.findings),
        "recommendations": len(report.recommendations),
        "chars": len(markdown),
    }
    return ToolResult(summary, update={"report": report, "report_markdown": markdown})
