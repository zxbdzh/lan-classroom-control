from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tag_build_creates_draft_release_with_generated_notes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "Get-Content RELEASE.md -Raw -Encoding utf8" in workflow
    assert "--draft --generate-notes --verify-tag" in workflow
    assert "--title \"Release $tag\" --notes $releaseNotes" in workflow
    assert "--notes-file RELEASE.md" not in workflow


def test_tag_build_can_replace_assets_on_an_existing_draft() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "gh release view $tag --json isDraft --jq '.isDraft'" in workflow
    assert 'if ($isDraft.Trim() -ne "true")' in workflow
    assert "refusing to overwrite its assets" in workflow
    assert "gh release upload $tag $assets --clobber" in workflow

    upload_branch = workflow.split("if ($releaseExists) {", 1)[1].split(
        "else {", 1
    )[0]
    assert "Get-Content RELEASE.md" not in upload_branch
    assert "gh release edit" not in upload_branch


def test_release_signing_always_removes_temporary_private_key() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    signing_step = workflow.split("- name: Sign update manifests", 1)[1].split(
        "- name: Create or update draft release", 1
    )[0]
    assert "try {" in signing_step
    assert "finally {" in signing_step
    assert "Remove-Item -LiteralPath $keyPath -Force" in signing_step


def test_student_installer_updates_an_existing_service() -> None:
    installer = (ROOT / "installer" / "student.iss").read_text(encoding="utf-8")

    assert 'Parameters: "--startup auto update"' in installer
    assert "Check: ShouldUpdateService" in installer
    assert 'Parameters: "--startup auto install"' in installer
    assert "Check: ShouldInstallService" in installer
    assert "CurrentControlSet\\Services\\LanClassroomStudentV2" in installer


def test_release_blocks_pe_files_above_windows_7_minimum() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "Verify Windows 7 PE compatibility" in workflow
    assert "scripts/check_pe_compat.py --maximum-version 6.1" in workflow
    assert "LanClassroom-Teacher-Setup.exe" in workflow
    assert "LanClassroom-Student-Setup.exe" in workflow
