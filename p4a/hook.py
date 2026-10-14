"""Insert app-level Android manifest entries after p4a renders its template."""

import re
from pathlib import Path


def after_apk_build(toolchain):
    """Apply Android manifest workarounds before Gradle packages the app."""
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

    # The bugreport from Android 16 showed HWUI aborting while locking a
    # destroyed mutex during PythonActivity startup. Keep Android's View
    # renderer off for this SDL activity; Kivy still renders its app surface
    # through SDL/OpenGL.
    activity_pattern = re.compile(r"<activity(?=[\s>])[^>]*>", re.DOTALL)
    activity_matches = 0

    def disable_activity_hwui(match):
        nonlocal activity_matches
        tag = match.group(0)
        name = re.search(r"\bandroid:name\s*=\s*(['\"])(.*?)\1", tag)
        if name is None or not name.group(2).endswith("PythonActivity"):
            return tag

        activity_matches += 1
        attr = re.compile(r"(\bandroid:hardwareAccelerated\s*=\s*['\"])[^'\"]*(['\"])")
        if attr.search(tag):
            return attr.sub(r"\1false\2", tag, count=1)
        return tag[:-1] + ' android:hardwareAccelerated="false">'

    manifest, _ = activity_pattern.subn(disable_activity_hwui, manifest)
    if activity_matches != 1:
        raise RuntimeError(
            "Esperava encontrar exatamente uma activity PythonActivity no manifesto; "
            f"encontradas: {activity_matches} ({manifest_path})"
        )

    if service not in manifest:
        if closing_tag not in manifest:
            raise RuntimeError(f"Tag {closing_tag} ausente em {manifest_path}")
        manifest = manifest.replace(closing_tag, f"{service}\n{closing_tag}", 1)

    manifest_path.write_text(manifest, encoding="utf-8")
