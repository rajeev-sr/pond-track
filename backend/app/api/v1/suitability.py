"""Suitability endpoints (M6-7, M6-11).

    POST /api/v1/suitability/weights/ahp    pairwise matrix -> weights + CR
    GET  /api/v1/suitability/weights        the weights the model ships with

The point of the POST is that it can say *no*. A district engineer who disagrees
with the shipped priorities supplies their own pairwise comparisons, and the
system refuses an internally inconsistent set rather than averaging it into
something that looks authoritative (HLD §6.5.2, and `400` in the status table is
specified for exactly this case).

Thin, as HLD 2.1 requires: the eigenvector, the Consistency Ratio and every
validation rule live in `services.ahp`.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from app.api.v1.contour import _options_form, _read_upload
from app.core.errors import NotFoundProblem, UnanswerableProblem, ValidationProblem
from app.core.logging import get_logger
from app.services import ahp
from app.services.contour_analysis import ContourAnalysisOptions
from app.services.siting import AHP_WEIGHTS, TIER_CRITERIA, tier_weights

log = get_logger("api.suitability")

router = APIRouter(prefix="/suitability", tags=["suitability"])


class AhpMatrixRequest(BaseModel):
    """A pairwise comparison matrix and the criteria it compares."""

    criteria: Annotated[
        list[str],
        Field(
            min_length=2,
            max_length=max(ahp.RANDOM_INDEX),
            description=(
                "Criterion names, in the same order as the matrix rows. Any "
                "names are accepted -- the arithmetic does not care what they "
                "mean -- but using the model's own names makes the result "
                "directly comparable to the shipped weights."
            ),
            examples=[["flow_accumulation", "slope", "depression_depth"]],
        ),
    ]
    matrix: Annotated[
        list[list[float]],
        Field(
            description=(
                "Row-major n x n matrix on Saaty's scale. `matrix[i][j]` is how "
                "much more important criterion i is than criterion j: 1 equal, "
                "3 moderately, 5 strongly, 7 very strongly, 9 extremely. The "
                "diagonal must be 1 and `matrix[j][i]` must be `1/matrix[i][j]`."
            ),
            examples=[[[1.0, 2.0, 4.0], [0.5, 1.0, 2.0], [0.25, 0.5, 1.0]]],
        ),
    ]
    strict: Annotated[
        bool,
        Field(
            description=(
                "When true (the default) a matrix with CR >= 0.10 is refused "
                "with 400. Set false to be told *how* inconsistent it is "
                "instead -- useful while revising a set of judgements."
            )
        ),
    ] = True


@router.get(
    "/weights",
    summary="The AHP weights the model ships with, and their consistency audit (M6-7)",
    description=(
        "Returns the shipped weight vector, the per-tier vectors derived from "
        "it, and the Consistency Ratio of the pairwise judgements it encodes.\n\n"
        "The audit is the reason this endpoint exists. Nine hardcoded numbers "
        "are unfalsifiable: a reader can disagree with them but cannot show they "
        "are *incoherent*. Reconstructing the pairwise matrix they imply, "
        "snapping it to the scale an expert would actually have used, and "
        "re-deriving the weights from it is what makes them auditable.\n\n"
        "The elicited table sums to 1.05 rather than 1.00 -- an arithmetic slip "
        "in the source, surfaced by this audit. The exported vector is "
        "normalised, which is ratio-preserving and so changes no score or "
        "ranking; both are reported so the discrepancy is visible rather than "
        "quietly corrected."
    ),
)
async def shipped_weights() -> Any:
    matrix = ahp.matrix_from_weights(AHP_WEIGHTS)
    audit = ahp.derive_weights(tuple(AHP_WEIGHTS), matrix, require_consistent=False)
    return {
        "weights": {k: round(v, 6) for k, v in AHP_WEIGHTS.items()},
        "sums_to": round(sum(AHP_WEIGHTS.values()), 10),
        "audit": audit.as_dict(),
        "reconstruction": {
            "note": (
                "The pairwise matrix implied by the shipped weights, rounded to "
                "the Saaty scale. Re-deriving from it recovers the weights only "
                "if they were coherent to begin with, which is what the "
                "Consistency Ratio above reports."
            ),
            "matrix": [[round(float(v), 4) for v in row] for row in matrix],
        },
        "per_tier": {
            tier: {
                "criteria": list(names),
                "weights": {k: round(v, 6) for k, v in tier_weights(tier).items()},
            }
            for tier, names in TIER_CRITERIA.items()
        },
        "source": "IMSD (NRSA/ISRO) practice, per HLD 6.5.1",
    }


@router.post(
    "/weights/ahp",
    summary="Derive criterion weights from a pairwise comparison matrix (M6-7)",
    description=(
        "Computes the principal eigenvector of a Saaty pairwise comparison "
        "matrix and returns it as a weight vector, together with the Consistency "
        "Index and Consistency Ratio.\n\n"
        "**An inconsistent matrix is refused with 400.** Judging A twice as "
        "important as B, B twice as important as C, and C more important than A "
        "is a contradiction no weight vector can express; CR is what detects it, "
        "and Saaty's threshold is 0.10. Returning weights anyway would dress a "
        "contradiction up as a recommendation.\n\n"
        "Two derivation methods are reported: the eigenvector, which is the "
        "definition, and the column-normalised row mean, which is the textbook "
        "hand calculation. Note that agreement between them is a check on the "
        "arithmetic, not evidence of consistency -- a symmetric cycle sends both "
        "to identical equal weights while CR exceeds 6."
    ),
    responses={
        400: {
            "description": (
                "The matrix is not a valid Saaty reciprocal matrix, or its "
                "Consistency Ratio is 0.10 or above."
            )
        }
    },
)
async def ahp_weights(body: AhpMatrixRequest) -> Any:
    try:
        derivation = ahp.derive_weights(
            tuple(body.criteria), body.matrix, require_consistent=body.strict
        )
    except ahp.InconsistentMatrixError as exc:
        raise ValidationProblem(
            detail=str(exc),
            errors=[
                {
                    "field": "matrix",
                    "message": (
                        f"consistency ratio {exc.consistency_ratio:.4f} is not below "
                        f"{ahp.MAX_CONSISTENCY_RATIO:.2f}"
                    ),
                }
            ],
            consistency_ratio=round(exc.consistency_ratio, 5),
            threshold=ahp.MAX_CONSISTENCY_RATIO,
        ) from exc
    except ValueError as exc:
        # Every other rejection from the service is a malformed matrix: not
        # square, not reciprocal, off the scale, wrong size for the names.
        raise ValidationProblem(
            detail=str(exc), errors=[{"field": "matrix", "message": str(exc)}]
        ) from exc

    log.info(
        "ahp weights derived",
        n=len(body.criteria),
        cr=round(derivation.consistency_ratio, 4),
        consistent=derivation.is_consistent,
    )
    return {"criteria": list(derivation.criteria), **derivation.as_dict()}


class SuitabilityWeights(BaseModel):
    """A caller's own weights, as a vector or as pairwise judgements."""

    weights: Annotated[
        dict[str, float] | None,
        Field(
            default=None,
            description=(
                "Criterion name to weight. Need not sum to 1 -- it is restricted "
                "to the criteria available for the tier and renormalised, so the "
                "score stays comparable to a default run."
            ),
        ),
    ] = None
    criteria: Annotated[
        list[str] | None,
        Field(default=None, description="Row order for `matrix`."),
    ] = None
    matrix: Annotated[
        list[list[float]] | None,
        Field(
            default=None,
            description=(
                "Pairwise comparisons on Saaty's scale instead of a vector. "
                "Refused with 400 if the Consistency Ratio reaches 0.10."
            ),
        ),
    ] = None


def _parse_weights(raw: str | None) -> dict[str, float] | None:
    """A weight vector from the `weights_json` form field, or None.

    Validated here so a bad matrix costs nothing: refusing before the upload is
    read and the pipeline starts is the difference between a 400 in milliseconds
    and one after a 24-second analysis.
    """
    if raw is None or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ValidationProblem(
            detail=f"weights_json is not valid JSON: {exc}",
            errors=[{"field": "weights_json", "message": str(exc)}],
        ) from exc
    if not isinstance(payload, dict):
        raise ValidationProblem(
            detail="weights_json must be a JSON object.",
            errors=[{"field": "weights_json", "message": "expected an object"}],
        )

    body = SuitabilityWeights.model_validate(payload)
    if body.matrix is not None:
        if not body.criteria:
            raise ValidationProblem(
                detail="a pairwise matrix needs `criteria` naming its rows, in order.",
                errors=[{"field": "criteria", "message": "required alongside `matrix`"}],
            )
        try:
            derived = ahp.derive_weights(tuple(body.criteria), body.matrix).weights
        except ahp.InconsistentMatrixError as exc:
            raise ValidationProblem(
                detail=str(exc),
                errors=[{"field": "matrix", "message": "consistency ratio is not below 0.10"}],
                consistency_ratio=round(exc.consistency_ratio, 5),
                threshold=ahp.MAX_CONSISTENCY_RATIO,
            ) from exc
        except ValueError as exc:
            raise ValidationProblem(
                detail=str(exc), errors=[{"field": "matrix", "message": str(exc)}]
            ) from exc
        missing = [name for name in AHP_WEIGHTS if name not in derived]
        if missing:
            raise ValidationProblem(
                detail=(
                    f"the matrix compares {list(derived)}, which leaves {missing} "
                    "unweighted. Compare every criterion the model scores."
                ),
                errors=[{"field": "criteria", "message": f"missing: {', '.join(missing)}"}],
            )
        return derived

    if not body.weights:
        raise ValidationProblem(
            detail="give either `weights` or `criteria` plus `matrix`.",
            errors=[{"field": "weights_json", "message": "no weights supplied"}],
        )
    supplied = {str(k): float(v) for k, v in body.weights.items()}

    # A vector must cover every criterion the model knows, not just the ones
    # this analysis happens to use. Which subset goes live depends on whether the
    # providers answer, and that is not known until enrichment has run -- so
    # checking against the full set is the only test that can be made here
    # rather than twenty-four seconds later, inside the job.
    missing = [name for name in AHP_WEIGHTS if name not in supplied]
    if missing:
        raise ValidationProblem(
            detail=(
                f"weights are missing for {missing}. Supply one for every "
                f"criterion the model knows ({list(AHP_WEIGHTS)}); which subset "
                "is used depends on the tier the providers allow, so a partial "
                "vector cannot be checked until the analysis is already running."
            ),
            errors=[{"field": "weights", "message": f"missing: {', '.join(missing)}"}],
        )
    unknown = [name for name in supplied if name not in AHP_WEIGHTS]
    if unknown:
        raise ValidationProblem(
            detail=f"unknown criteria {unknown}; the model scores {list(AHP_WEIGHTS)}.",
            errors=[{"field": "weights", "message": f"unknown: {', '.join(unknown)}"}],
        )
    if any(v < 0 for v in supplied.values()):
        raise ValidationProblem(
            detail="weights cannot be negative.",
            errors=[{"field": "weights", "message": "negative weight"}],
        )
    if sum(supplied.values()) <= 0:
        raise ValidationProblem(
            detail="the weights sum to zero, so nothing would be scored.",
            errors=[{"field": "weights", "message": "sums to zero"}],
        )
    return supplied


@router.post(
    "/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the suitability engine as a job, optionally with your own weights (M6-11)",
    description=(
        "Starts an analysis job and returns `202` with a `job_id`, exactly as "
        "`POST /analysis` does -- poll the same status URL. What this adds is the "
        "weight override.\n\n"
        '`weights_json` carries either a vector, `{"weights": {"slope": 0.5, '
        '...}}`, or pairwise judgements, `{"criteria": [...], "matrix": '
        "[[...]]}`, and the system derives the weights from the matrix itself. It "
        "is a JSON string in a form field rather than a JSON body because the "
        "request also carries the uploaded file.\n\n"
        "Both forms are validated **before any work starts**: an inconsistent "
        "matrix is refused with 400 and its Consistency Ratio, and a vector that "
        "omits a live criterion is refused naming what it left out -- scoring "
        "with weights that ignore an available criterion produces a number that "
        "cannot be compared to a default run.\n\n"
        "Fetch the ranking from `GET /suitability/{job_id}/sites` once the job is "
        "terminal, or the whole document from `GET /analysis/{job_id}/result`."
    ),
    responses={400: {"description": "The weights or the pairwise matrix were refused."}},
)
async def analyze_suitability(
    background: BackgroundTasks,
    response: Response,
    file: Annotated[UploadFile, File(description="Contour map as KML or KMZ.")],
    opts: Annotated[ContourAnalysisOptions, Depends(_options_form)],
    weights_json: Annotated[
        str | None,
        Form(
            description=(
                'Either {"weights": {name: value, ...}} or '
                '{"criteria": [...], "matrix": [[...]]}. Omit to use the shipped '
                "AHP vector."
            )
        ),
    ] = None,
) -> Any:
    from app.api.v1.analysis import _options_dict

    override = _parse_weights(weights_json)
    data, filename = await _read_upload(file)
    options = _options_dict(opts)
    if override is not None:
        options["weights_override"] = override

    job_id = uuid.uuid4().hex
    status_url = f"/api/v1/analysis/{job_id}/status"
    response.headers["Location"] = status_url

    import time as _time

    from app.services.job_runner import run_analysis_job
    from app.services.job_store import JobRecord, get_store
    from app.services.jobs import JobProgress

    now = _time.time()
    get_store().put(
        JobRecord(
            job_id=job_id,
            progress=JobProgress().as_dict(),
            params={k: v for k, v in options.items() if k != "weights_override"},
            created_at=now,
            updated_at=now,
        )
    )
    background.add_task(run_analysis_job, job_id, data, filename, options)
    log.info("suitability job accepted", job_id=job_id, custom_weights=override is not None)
    return {
        "job_id": job_id,
        "state": "queued",
        "status_url": status_url,
        "sites_url": f"/api/v1/suitability/{job_id}/sites",
        "result_url": f"/api/v1/analysis/{job_id}/result",
        "weights_applied": (
            {k: round(v, 6) for k, v in override.items()}
            if override is not None
            else "shipped defaults"
        ),
    }


@router.get(
    "/{job_id}/sites",
    summary="Ranked candidate sites with per-criterion contributions (M6-11, FR-9)",
    description=(
        "The ranked-sites projection of a finished analysis job.\n\n"
        "Narrower than `GET /analysis/{job_id}/result` on purpose: a client "
        "comparing sites wants the ranking, the score and *why* each site scored "
        "as it did, not the contour geometry and the rainfall series alongside "
        "it.\n\n"
        "`criteria_breakdown` carries the weight and contribution of every "
        "criterion per site. A score of 72 with no breakdown is unauditable, and "
        "being auditable is the whole point of an AHP model.\n\n"
        "Served for `partial` as well as `done`: a ranking computed without soil "
        "data is still a ranking, and the tier travels with it so nobody compares "
        "it to a full-tier score by mistake."
    ),
)
async def job_sites(job_id: str) -> Any:
    from app.services.job_store import get_store

    record = get_store().get(job_id)
    if record is None:
        raise NotFoundProblem(detail=f"no analysis job with id {job_id!r}.", job_id=job_id)
    state = record.progress.get("state")
    if state not in ("done", "partial"):
        raise UnanswerableProblem(
            detail=(
                f"job {job_id!r} is {state!r}, so it has no sites yet. Poll "
                f"/api/v1/analysis/{job_id}/status until `is_terminal` is true."
            ),
            job_id=job_id,
            state=state,
        )

    result = record.result or {}
    sites = result.get("candidate_sites") or []
    suitability = result.get("suitability") or {}
    return {
        "job_id": job_id,
        "state": state,
        # The tier travels with the ranking: scores are not comparable across
        # tiers, because a different set of criteria produced them.
        "analysis_tier": suitability.get("analysis_tier")
        or (result.get("environment") or {}).get("analysis_tier"),
        "criteria_weights": suitability.get("criteria_weights"),
        "site_count": len(sites),
        "recommended_site": result.get("recommended_site"),
        "sites": [
            {
                "rank": site.get("rank"),
                "suitability_score": site.get("suitability_score"),
                "site_kind": site.get("site_kind"),
                "location": site.get("location"),
                "criteria_breakdown": site.get("criteria_breakdown"),
            }
            for site in sites
        ],
        "warnings": record.progress.get("warnings", []),
    }
