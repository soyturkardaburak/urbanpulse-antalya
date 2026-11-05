from __future__ import annotations

import typer

from urbanpulse.accessibility import run_accessibility
from urbanpulse.catalog import download_gtfs
from urbanpulse.destinations import write_destinations
from urbanpulse.importer import import_phase1
from urbanpulse.osm import crop_study_osm, download_turkey_osm
from urbanpulse.paths import raw_gtfs_dir, validation_html_path
from urbanpulse.timetable_check import run_spot_check
from urbanpulse.validate import run_validation

app = typer.Typer(no_args_is_help=True, help="UrbanPulse Antalya research pipeline.")
data_app = typer.Typer(no_args_is_help=True, help="Download, validate, and import source data.")
app.add_typer(data_app, name="data")


@data_app.command("download")
def data_download(force: bool = typer.Option(False, "--force", help="Replace the pinned GTFS zip.")) -> None:
    """Download the Antalya KentKart-derived GTFS zip and record its hash."""
    catalog = download_gtfs(force=force)
    typer.echo(f"GTFS pin {catalog['content_sha256']}")
    typer.echo(f"Retrieved {catalog['retrieved_at']}")
    typer.echo("The zip is stored under data/raw and is not part of the git tree.")


@data_app.command("download-osm")
def data_download_osm(force: bool = typer.Option(False, "--force")) -> None:
    """Download the Geofabrik Turkey PBF and verify its MD5."""
    catalog = download_turkey_osm(force=force)
    typer.echo(f"Turkey PBF pin {catalog['content_sha256']}")
    typer.echo(f"Size {catalog['byte_size']} bytes")


@data_app.command("validate")
def data_validate() -> None:
    """Validate the feed, delineate the study area, and write the route register."""
    if not raw_gtfs_dir().joinpath("stops.txt").exists():
        raise typer.BadParameter("GTFS tables are missing. Run urbanpulse data download first.")
    summary = run_validation(raw_gtfs_dir())
    study = summary.get("study_area", {})
    decisions = summary.get("route_decisions", {})
    issues = summary.get("issues", [])
    typer.echo(f"Study area {study.get('area_km2')} km²")
    typer.echo(f"Stops inside {study.get('stops_inside')} / outside {study.get('stops_outside')}")
    typer.echo(f"Routes included {decisions.get('included')} / excluded {decisions.get('excluded')}")
    for point in study.get("control_points", []):
        if point["role"] == "required":
            state = "pass" if point["passed"] else "FAIL"
            typer.echo(f"Control {point['name']}: {state}")
    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    typer.echo(f"Errors {errors}. Warnings {warnings}.")
    typer.echo("Scheduled accessibility was not calculated.")
    typer.echo(f"Report {validation_html_path()}")
    if not study.get("sane", False):
        raise typer.Exit(code=2)
    if errors:
        raise typer.Exit(code=1)


@data_app.command("osm-crop")
def data_osm_crop() -> None:
    """Crop the Turkey PBF to the study-area bounding box plus a margin."""
    catalog = crop_study_osm()
    typer.echo(catalog["geographic_coverage"])
    typer.echo(f"Extract pin {catalog['content_sha256']}")


@data_app.command("import")
def data_import() -> None:
    """Load the validated feed, study area, and route register into PostGIS."""
    import_phase1(echo=typer.echo)


@app.command("timetable-check")
def timetable_check() -> None:
    """Compare 20 published timetable patterns with the pinned GTFS."""
    summary = run_spot_check()
    typer.echo(summary["interpretation_reason"])
    typer.echo(f"Interpret hospital times: {summary['interpretation_allowed']}")


@app.command("destinations")
def destinations() -> None:
    """Write the reviewed hospital register."""
    rows = write_destinations()
    included = sum(1 for row in rows if row["include_in_routing"] == "true")
    typer.echo(f"Wrote {len(rows)} hospital rows, {included} included for routing.")


@app.command("accessibility")
def accessibility() -> None:
    """Prepare and run the Wednesday-morning hospital measurement."""
    summary = run_accessibility()
    typer.echo(f"Origins {summary.get('origins')}")
    typer.echo(f"Cells with a hospital time {summary.get('with_hospital_time')}")
    typer.echo(f"Interpret as access: {summary.get('interpretation_allowed')}")


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve the read-only Phase 1 API."""
    import uvicorn

    uvicorn.run("urbanpulse.api:app", host=host, port=port)


if __name__ == "__main__":
    app()
