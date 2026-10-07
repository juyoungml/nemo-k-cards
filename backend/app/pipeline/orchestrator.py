"""Runs one job through SPEC §4: research -> verify -> write -> render -> QA -> review."""

from app.schemas import Job


async def run_job(job: Job) -> None:
    # TODO: for each stage, update job.status and persist
    #   1. RESEARCHING  agent_runner.run_stage("researcher", ...) -> list[EventBrief]
    #   2. VERIFYING    services.link_checker.verify(...)          -> VerificationReport
    #   3. WRITING      agent_runner.run_stage("copywriter", ...)  -> CardDeck
    #   4. RENDERING    renderer.render(...)                       -> list[RenderedSlide]
    #   5. QA           services.visual_qa.run_qa(...)             -> QAReport
    #   6. REVIEWING    agent_runner.run_stage("reviewer", ...)    -> ReviewVerdict
    #   -> READY_FOR_REVIEW | REJECTED
    raise NotImplementedError
