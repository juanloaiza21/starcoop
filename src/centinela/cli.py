"""``centinela`` — the operator's command line.

Only offline commands exist today. Nothing here can reach the live platform, which is
deliberate: the read-only observer phase comes before any code that can write.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer

from centinela.config.floor import FloorSheet, FloorSheetError
from centinela.domain.money import format_cop
from centinela.simulation import simulate
from centinela.strategy.engine import Policy

app = typer.Typer(
    add_completion=False,
    help="Bidding agent for SECOP II reverse auctions. Offline commands only, for now.",
)


@app.command("check-floor")
def check_floor(
    sheet: Annotated[Path, typer.Argument(help="Path to a floor sheet YAML file.")],
) -> None:
    """Validate a floor sheet and show what it authorises.

    Run this before every auction. It cannot tell you the number is right — only a
    second person recomputing it independently can do that — but it will catch an
    unsigned, inconsistent, or misaddressed sheet.
    """
    try:
        loaded = FloorSheet.load(sheet)
    except FloorSheetError as exc:
        typer.secho(f"REFUSED: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho("Floor sheet is internally valid.", fg=typer.colors.GREEN)
    typer.echo(f"  tender          {loaded.tender}")
    typer.echo(f"  auction         {loaded.auction_id}")
    typer.echo(f"  total floor     {format_cop(loaded.total_floor)}")
    typer.echo(f"  computed by     {loaded.computed_by} on {loaded.computed_on}")
    typer.echo(f"  approved by     {loaded.approved_by} on {loaded.approved_on}")
    typer.echo(f"  basis           {loaded.basis.strip()}")

    if not loaded.had_independent_check:
        typer.secho(
            "\nNo independent recomputation is recorded. A wrong floor defeats every "
            "automated guard from above, so have a second person recompute it by a "
            "different method before arming for live bidding.",
            fg=typer.colors.YELLOW,
        )


@app.command("replay")
def replay(
    fixture: Annotated[Path, typer.Argument(help="A captured auction replay fixture.")],
    floor_sheet: Annotated[
        Path | None, typer.Option("--floor-sheet", help="Floor sheet to bid against.")
    ] = None,
    floor_cop: Annotated[
        str | None, typer.Option("--floor-cop", help="Floor in pesos, for quick trials.")
    ] = None,
    fire_at: Annotated[
        float, typer.Option("--fire-at", help="Seconds before close to place the lance.")
    ] = 90.0,
    limit: Annotated[int, typer.Option("--limit", help="Rows of timeline to print.")] = 40,
) -> None:
    """Replay a captured auction and print what the bot would have decided.

    This is the dress rehearsal. It answers "what would we have done, and why" against
    real events, real timings and real competitor behaviour, without sending anything.
    """
    if floor_sheet is None and floor_cop is None:
        typer.secho("Give either --floor-sheet or --floor-cop.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if floor_sheet is not None:
        try:
            floor = FloorSheet.load(floor_sheet).total_floor
        except FloorSheetError as exc:
            typer.secho(f"REFUSED: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    else:
        assert floor_cop is not None
        from centinela.domain.money import Centavos

        floor = Centavos(int(Decimal(floor_cop) * 100))

    data: dict[str, Any] = json.loads(fixture.read_text(encoding="utf-8"), parse_float=Decimal)
    report = simulate(data, floor=floor, policy=Policy(fire_at_seconds_before_end=fire_at))

    typer.echo(f"Floor: {format_cop(floor)}   Fire at: T-{fire_at:.0f}s\n")
    typer.echo(report.render(limit=limit))
    typer.echo("")
    typer.echo(f"Decision changes : {len(report.decisions)}")
    typer.echo(f"Bids submitted   : {report.bids_that_would_have_been_submitted}")
    typer.echo(f"Alarms raised    : {report.alarms_raised}")
    if report.best_competitor_total is not None:
        typer.echo(f"Best rival bid   : {format_cop(report.best_competitor_total)}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
