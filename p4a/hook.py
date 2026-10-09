"""Insert app-level Android manifest entries after p4a renders its template."""

from pathlib import Path


def after_apk_build(toolchain):
    """Add the Media3 playback service before Gradle processes the manifest."""
    dist_dir = Path(toolchain._dist.dist_dir)
    manifest_path = dist_dir / "src" / "main" / "AndroidManifest.xml"
    service_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "android"
        / "extra_manifest_application_xml.xml"
    )

    manifest = manifest_path.read_text(encoding="utf-8")
    service = service_path.read_text(encoding="utf-8").strip()
    closing_tag = "</application>"

    if service in manifest:
        return
    if closing_tag not in manifest:
        raise RuntimeError(f"Tag {closing_tag} ausente em {manifest_path}")

    manifest_path.write_text(
        manifest.replace(closing_tag, f"{service}\n{closing_tag}", 1),
        encoding="utf-8",
    )
