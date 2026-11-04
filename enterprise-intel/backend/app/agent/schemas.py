from typing import Any

from pydantic import BaseModel, Field

from app.rag.schemas import Conflict


class Finding(BaseModel):
    point: str
    evidence: list[str] = Field(default=[], description="Source ids such as S1")
    frequency: int = 1


class AnalysisResult(BaseModel):
    analysis_type: str = ""
    findings: list[Finding] = []
    conflicts: list[Conflict] = []
    gaps: list[str] = []


class ReportSection(BaseModel):
    heading: str
    detail: str
    evidence: list[str] = []


class Report(BaseModel):
    title: str
    executive_summary: str
    findings: list[ReportSection] = []
    recommendations: list[str] = []
    missing_info: list[str] = []


class NextAction(BaseModel):
    thought: str = ""
    tool: str
    args: dict[str, Any] = {}


def render_report(report: Report) -> str:
    def cite(ids: list[str]) -> str:
        return "".join(f" [{i}]" for i in ids)

    lines = [f"# {report.title}", "", "## Executive summary", report.executive_summary, "", "## Findings"]
    for f in report.findings:
        lines += [f"### {f.heading}", f"{f.detail}{cite(f.evidence)}", ""]
    if report.recommendations:
        lines += ["## Recommendations", *[f"- {r}" for r in report.recommendations], ""]
    if report.missing_info:
        lines += ["## Missing information", *[f"- {m}" for m in report.missing_info]]
    return "\n".join(lines).strip()


def render_analysis(analysis: AnalysisResult) -> str:
    lines = [f"## Analysis ({analysis.analysis_type})"]
    for f in analysis.findings:
        lines.append(f"- {f.point}{''.join(f' [{i}]' for i in f.evidence)}")
    if analysis.conflicts:
        lines += ["", "### Conflicts", *[f"- **{c.topic}**: {c.description}" for c in analysis.conflicts]]
    if analysis.gaps:
        lines += ["", "### Gaps", *[f"- {g}" for g in analysis.gaps]]
    return "\n".join(lines)
