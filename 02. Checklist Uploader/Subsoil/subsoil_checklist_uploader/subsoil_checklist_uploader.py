
from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


# ---------------------------------------------------------------------------
# PROJECT SETTINGS
# ---------------------------------------------------------------------------

SUBSOIL_ROOT = Path(
    r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop"
    r"\INREP36600 - Tweefontein TSF Project Files"
    r"\15 Quality\6. Inspections\4. Subsoil Pipes"
)

SYSTEMS_URL = "https://systems.stefstocks.com/web/login"
WAIT_SECONDS = 30

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
PDF_EXTENSIONS = {".pdf"}

RN_PATTERN = re.compile(r"\bRN\s*[-_ ]?\s*0*(\d+)\b", re.IGNORECASE)
LEADING_NUMBER = re.compile(r"^\s*(\d+)")

# Ordered evidence slots.
PHOTO_SLOT_MAP = {
    "drilled": {
        1: "Excavation",
        2: "Bedding + Pipe Lay - Pipe Length",
        3: "Bedding + Pipe Lay - Weld",
        4: "Blanket Layer Backfill - 19mm Stone",
        5: "Bidim Wrap",
        6: "Density / Insitu Backfill - Layer 1",
    },
    "solid": {
        1: "Excavation",
        2: "Bedding + Pipe Lay - Pipe Length",
        3: "Bedding + Pipe Lay - Weld",
        4: "Blanket Layer Backfill - Loose Insitu Material",
        5: "Density / Insitu Backfill - Layer",
        # Slot 6 intentionally left untouched.
    },
}


# ---------------------------------------------------------------------------
# DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class SubsoilPackage:
    rn: str
    system_name: str
    section_name: str
    photo_folder: Path
    photos: list[Path]
    invert_pdf: Path | None


# ---------------------------------------------------------------------------
# LOCAL FILE DISCOVERY
# ---------------------------------------------------------------------------

def extract_rn(value: str) -> str | None:
    match = RN_PATTERN.search(value)
    return str(int(match.group(1))) if match else None


def natural_photo_key(path: Path) -> tuple[int, str]:
    """
    Photos are intentionally ordered by their filename.

    Examples:
        1.jpeg
        2.jpeg
        3.jpeg
        4.jpeg

    If a filename does not begin with a number, it is placed after numbered
    files in alphabetical order.
    """
    match = LEADING_NUMBER.match(path.stem)
    number = int(match.group(1)) if match else 999999
    return number, path.name.lower()


def list_images(folder: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ],
        key=natural_photo_key,
    )


def find_invert_pdf(system_folder: Path, rn: str) -> Path | None:
    invert_root = system_folder / "3. Invert Levels"

    if not invert_root.exists():
        return None

    candidates: list[Path] = []

    # Prefer PDFs inside an RN-labelled invert folder.
    for path in invert_root.rglob("*.pdf"):
        parent_rn = extract_rn(str(path.parent))
        file_rn = extract_rn(path.stem)

        if parent_rn == rn or file_rn == rn:
            candidates.append(path)

    if not candidates:
        return None

    # Prefer filenames that explicitly look like as-built / invert survey files.
    candidates.sort(
        key=lambda path: (
            0 if any(
                keyword in path.name.lower()
                for keyword in ("as-built", "as built", "invert", "survey")
            ) else 1,
            path.name.lower(),
        )
    )

    return candidates[0]


def discover_packages(root: Path) -> list[SubsoilPackage]:
    """
    Expected structure:

        4. Subsoil Pipes
        ├── 1. TSF
        │   ├── 3. Invert Levels
        │   └── 5. Photos
        ├── 2. RWD
        │   ├── 3. Invert Levels
        │   └── 5. Photos
        └── 3. Silt Trap
            ├── 3. Invert Levels
            └── 5. Photos

    Each evidence package is identified by an RN-labelled folder beneath
    "5. Photos", e.g.:
        RN153 - STSS1 to STSS8
    """
    if not root.exists():
        raise FileNotFoundError(f"Subsoil root folder not found:\n{root}")

    packages: list[SubsoilPackage] = []

    system_folders = sorted(
        [path for path in root.iterdir() if path.is_dir()],
        key=lambda path: path.name.lower(),
    )

    for system_folder in system_folders:
        photos_root = system_folder / "5. Photos"

        if not photos_root.exists():
            continue

        for photo_folder in sorted(
            [path for path in photos_root.iterdir() if path.is_dir()],
            key=lambda path: path.name.lower(),
        ):
            rn = extract_rn(photo_folder.name)

            if not rn:
                continue

            photos = list_images(photo_folder)
            invert_pdf = find_invert_pdf(system_folder, rn)

            section_name = RN_PATTERN.sub("", photo_folder.name).strip(" -_")

            packages.append(
                SubsoilPackage(
                    rn=rn,
                    system_name=system_folder.name,
                    section_name=section_name,
                    photo_folder=photo_folder,
                    photos=photos,
                    invert_pdf=invert_pdf,
                )
            )

    return packages


def validate_package(package: SubsoilPackage, pipe_type: str) -> tuple[bool, list[str]]:
    required_count = len(PHOTO_SLOT_MAP[pipe_type])
    issues: list[str] = []

    if len(package.photos) < required_count:
        issues.append(
            f"{required_count} photos are required for a {pipe_type} pipe, "
            f"but only {len(package.photos)} were found."
        )

    if len(package.photos) > required_count:
        issues.append(
            f"{len(package.photos)} photos were found. Only the first "
            f"{required_count} will be used according to filename order."
        )

    if package.invert_pdf is None:
        issues.append("No matching invert/as-built PDF was found.")

    blocking = len(package.photos) < required_count
    return not blocking, issues


# ---------------------------------------------------------------------------
# SELENIUM HELPERS
# ---------------------------------------------------------------------------

def scroll_to_element(driver: webdriver.Chrome, element) -> None:
    driver.execute_script(
        """
        arguments[0].scrollIntoView({
            block: 'center',
            inline: 'nearest'
        });
        """,
        element,
    )


def start_chrome() -> webdriver.Chrome:
    options = Options()
    options.add_experimental_option("detach", True)

    service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(
        service=service,
        options=options,
    )

    driver.maximize_window()
    return driver


def get_readonly_field_value(driver: webdriver.Chrome, label_text: str) -> str:
    """
    Read an Angular Material field by its mat-label.
    Returns an empty string when the field cannot be resolved.
    """
    try:
        label = driver.find_element(
            By.XPATH,
            f"//mat-label[normalize-space()='{label_text}']",
        )

        field = label.find_element(
            By.XPATH,
            "./ancestor::mat-form-field[1]",
        )

        input_element = field.find_element(
            By.XPATH,
            ".//input | .//textarea",
        )

        return (input_element.get_attribute("value") or "").strip()

    except Exception:
        return ""


def verify_correct_checklist(driver: webdriver.Chrome, rn: str) -> None:
    """
    Safety check. Inspection Request No. should normally correspond to the RN.
    If the webpage value is readable and does not contain the chosen RN, stop.
    """
    value = get_readonly_field_value(driver, "Inspection Request No.")

    if not value:
        print(
            "WARNING: Could not read the online Inspection Request No. "
            "Automatic RN verification was skipped."
        )
        return

    online_rn = extract_rn(value)

    if online_rn is None:
        digits = re.sub(r"\D", "", value)
        online_rn = str(int(digits)) if digits else None

    if online_rn and online_rn != rn:
        raise RuntimeError(
            f"WRONG CHECKLIST OPEN.\n\n"
            f"Selected local package: RN{rn}\n"
            f"Online Inspection Request No.: {value}\n\n"
            "No photos were uploaded."
        )

    print(f"Checklist verification passed: online request = {value}")


# ---------------------------------------------------------------------------
# PHOTO UPLOAD
# ---------------------------------------------------------------------------

def open_sketches_photos_panel(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, WAIT_SECONDS)

    title = wait.until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//*[normalize-space()='Sketches/Photos (Required)']",
            )
        )
    )

    panel = title.find_element(
        By.XPATH,
        "./ancestor::mat-expansion-panel[1]",
    )

    header = panel.find_element(
        By.CSS_SELECTOR,
        "mat-expansion-panel-header",
    )

    scroll_to_element(driver, header)

    if header.get_attribute("aria-expanded") != "true":
        wait.until(EC.element_to_be_clickable(header)).click()

    return panel


def get_photo_section(
    driver: webdriver.Chrome,
    panel,
    photo_number: int,
):
    wait = WebDriverWait(driver, WAIT_SECONDS)
    heading_text = f"Sketch/Photo {photo_number}"

    heading = wait.until(
        lambda _: panel.find_element(
            By.XPATH,
            f".//h3[normalize-space()='{heading_text}']",
        )
    )

    section = heading.find_element(
        By.XPATH,
        "./ancestor::div[.//mat-select][1]",
    )

    return section


def handle_replace_confirmation(driver: webdriver.Chrome) -> None:
    try:
        short_wait = WebDriverWait(driver, 3)

        dialog = short_wait.until(
            EC.visibility_of_element_located(
                (
                    By.XPATH,
                    "//mat-dialog-container["
                    ".//*[contains(normalize-space(), 'Replace Sketch')]"
                    "]",
                )
            )
        )

        yes_button = dialog.find_element(
            By.XPATH,
            ".//button[.//*[normalize-space()='Yes'] or normalize-space()='Yes']",
        )

        yes_button.click()

        WebDriverWait(driver, WAIT_SECONDS).until(
            EC.invisibility_of_element(dialog)
        )

    except TimeoutException:
        pass


def select_photo_type(
    driver: webdriver.Chrome,
    section,
    photo_number: int,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    mat_select = section.find_element(By.XPATH, ".//mat-select[1]")
    current_value = mat_select.text.strip()

    if current_value.lower() == "photo":
        return

    scroll_to_element(driver, mat_select)
    wait.until(lambda _: mat_select.is_displayed() and mat_select.is_enabled())
    mat_select.click()

    photo_option = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//mat-option["
                ".//*[normalize-space()='Photo'] "
                "or normalize-space()='Photo'"
                "]",
            )
        )
    )

    photo_option.click()
    handle_replace_confirmation(driver)


def get_photo_file_input(
    driver: webdriver.Chrome,
    panel,
    photo_number: int,
):
    wait = WebDriverWait(driver, WAIT_SECONDS)

    section = get_photo_section(
        driver=driver,
        panel=panel,
        photo_number=photo_number,
    )

    return wait.until(
        lambda _: section.find_element(
            By.CSS_SELECTOR,
            "input[type='file']",
        )
    )


def upload_checklist_photo(
    driver: webdriver.Chrome,
    panel,
    photo_number: int,
    image_path: Path,
) -> None:
    section = get_photo_section(
        driver=driver,
        panel=panel,
        photo_number=photo_number,
    )

    select_photo_type(
        driver=driver,
        section=section,
        photo_number=photo_number,
    )

    file_input = get_photo_file_input(
        driver=driver,
        panel=panel,
        photo_number=photo_number,
    )

    driver.execute_script(
        """
        arguments[0].removeAttribute('hidden');
        arguments[0].style.display = 'block';
        arguments[0].style.visibility = 'visible';
        arguments[0].style.opacity = '1';
        """,
        file_input,
    )

    scroll_to_element(driver, file_input)
    file_input.send_keys(str(image_path.resolve()))

    print(
        f"Sketch/Photo {photo_number}: "
        f"{image_path.name} passed to webpage."
    )


def upload_photo_package(
    driver: webdriver.Chrome,
    package: SubsoilPackage,
    pipe_type: str,
) -> None:
    slot_map = PHOTO_SLOT_MAP[pipe_type]
    panel = open_sketches_photos_panel(driver)

    for slot_number, milestone in slot_map.items():
        image_path = package.photos[slot_number - 1]

        print(
            f"\nSlot {slot_number}: {milestone}\n"
            f"  File: {image_path.name}"
        )

        upload_checklist_photo(
            driver=driver,
            panel=panel,
            photo_number=slot_number,
            image_path=image_path,
        )


# ---------------------------------------------------------------------------
# OPTIONAL SUPPORTING DOCUMENT - INVERT SURVEY
# ---------------------------------------------------------------------------

def open_optional_supporting_documents(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, WAIT_SECONDS)

    title = wait.until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//*[normalize-space()='Optional Supporting Documents']",
            )
        )
    )

    panel = title.find_element(
        By.XPATH,
        "./ancestor::mat-expansion-panel[1]",
    )

    header = panel.find_element(
        By.CSS_SELECTOR,
        "mat-expansion-panel-header",
    )

    scroll_to_element(driver, header)

    if header.get_attribute("aria-expanded") != "true":
        wait.until(EC.element_to_be_clickable(header)).click()

    return panel


def click_add_button(driver: webdriver.Chrome, panel) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    add_button = wait.until(
        lambda _: panel.find_element(
            By.XPATH,
            ".//button[.//*[normalize-space()='Add']]",
        )
    )

    scroll_to_element(driver, add_button)
    wait.until(lambda _: add_button.is_displayed() and add_button.is_enabled())
    add_button.click()


def get_supporting_document_dialog(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, WAIT_SECONDS)

    return wait.until(
        EC.visibility_of_element_located(
            (
                By.XPATH,
                "//mat-dialog-container["
                ".//*[contains(normalize-space(), 'Checklist Supporting Document')]"
                "]",
            )
        )
    )


def select_supporting_document_type(
    driver: webdriver.Chrome,
    dialog,
) -> str:
    """
    Try the most appropriate available document type.

    The exact options are loaded dynamically by the web application, so this
    function checks the live list instead of assuming that 'Survey' exists.

    Preference:
        Survey
        As Built / As-Built
        Other
        Test Results
    """
    wait = WebDriverWait(driver, WAIT_SECONDS)

    document_type = wait.until(
        lambda _: dialog.find_element(By.XPATH, ".//mat-select")
    )

    document_type.click()

    wait.until(
        lambda _: len(driver.find_elements(By.XPATH, "//mat-option")) > 0
    )

    options = driver.find_elements(By.XPATH, "//mat-option")
    option_text = {
        option.text.strip().lower(): option
        for option in options
        if option.text.strip()
    }

    preferred = [
        "survey",
        "as built",
        "as-built",
        "other",
        "test results",
    ]

    for preferred_text in preferred:
        for actual_text, option in option_text.items():
            if preferred_text == actual_text or preferred_text in actual_text:
                option.click()
                return option.text.strip()

    available = ", ".join(
        sorted(text for text in option_text.keys())
    )

    # Close the dropdown before raising the error.
    webdriver.ActionChains(driver).send_keys("\u001b").perform()

    raise RuntimeError(
        "No suitable supporting-document type was found for the invert survey.\n"
        f"Available document types: {available}"
    )


def enter_supporting_description(
    driver: webdriver.Chrome,
    dialog,
    description: str,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    description_field = wait.until(
        lambda _: dialog.find_element(
            By.XPATH,
            ".//mat-label[contains(normalize-space(), 'Description')]"
            "/ancestor::mat-form-field[1]//input"
            " | "
            ".//mat-label[contains(normalize-space(), 'Description')]"
            "/ancestor::mat-form-field[1]//textarea",
        )
    )

    description_field.clear()
    description_field.send_keys(description)


def click_select_document_and_save(
    driver: webdriver.Chrome,
    dialog,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    save_button = wait.until(
        lambda _: dialog.find_element(
            By.XPATH,
            ".//button["
            ".//*[contains(normalize-space(), 'Select Document and Save')]"
            "]",
        )
    )

    wait.until(
        lambda _: (
            save_button.is_displayed()
            and save_button.is_enabled()
            and save_button.get_attribute("disabled") is None
        )
    )

    scroll_to_element(driver, save_button)
    save_button.click()


def upload_supporting_document_file(
    driver: webdriver.Chrome,
    pdf_path: Path,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    inputs = wait.until(
        lambda _: driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='file']",
        )
    )

    if not inputs:
        raise RuntimeError(
            "No file input was found for the supporting document."
        )

    file_input = inputs[-1]

    driver.execute_script(
        """
        arguments[0].removeAttribute('hidden');
        arguments[0].style.display = 'block';
        arguments[0].style.visibility = 'visible';
        """,
        file_input,
    )

    file_input.send_keys(str(pdf_path.resolve()))


def upload_invert_survey(
    driver: webdriver.Chrome,
    package: SubsoilPackage,
) -> None:
    if package.invert_pdf is None:
        print("No invert survey PDF found. Invert upload skipped.")
        return

    panel = open_optional_supporting_documents(driver)
    click_add_button(driver, panel)

    dialog = get_supporting_document_dialog(driver)
    selected_type = select_supporting_document_type(driver, dialog)

    description_parts = [f"RN{package.rn}"]

    if package.section_name:
        description_parts.append(package.section_name)

    description_parts.append("Invert As-Built Survey")

    description = " - ".join(description_parts)

    enter_supporting_description(
        driver,
        dialog,
        description,
    )

    click_select_document_and_save(
        driver,
        dialog,
    )

    upload_supporting_document_file(
        driver,
        package.invert_pdf,
    )

    print(
        f"Invert survey passed to webpage.\n"
        f"  Type: {selected_type}\n"
        f"  Description: {description}\n"
        f"  File: {package.invert_pdf.name}"
    )


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class SubsoilUploaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("Subsoil Checklist Uploader")
        self.geometry("1120x700")
        self.minsize(930, 600)

        self.root_var = tk.StringVar(value=str(SUBSOIL_ROOT))
        self.pipe_type_var = tk.StringVar(value="drilled")
        self.status_var = tk.StringVar(value="Ready")

        self.packages: list[SubsoilPackage] = []
        self.selected_package: SubsoilPackage | None = None

        self._build_ui()
        self.scan_packages()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(
            root,
            text="Subsoil Checklist Uploader",
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            root,
            text=(
                "Loads milestone photos in filename order, uses 6 slots for drilled "
                "pipe and 5 slots for solid pipe, then uploads the matching invert "
                "as-built PDF as a supporting document."
            ),
        ).pack(anchor="w", pady=(2, 12))

        folder_frame = ttk.LabelFrame(
            root,
            text="Subsoil evidence directory",
            padding=10,
        )
        folder_frame.pack(fill="x")

        ttk.Entry(
            folder_frame,
            textvariable=self.root_var,
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            folder_frame,
            text="Rescan",
            command=self.scan_packages,
        ).pack(side="left", padx=(8, 0))

        options = ttk.Frame(root)
        options.pack(fill="x", pady=10)

        ttk.Label(options, text="Pipe type:").pack(side="left")

        ttk.Radiobutton(
            options,
            text="Drilled / Perforated (6 photos)",
            variable=self.pipe_type_var,
            value="drilled",
            command=self.refresh_preview,
        ).pack(side="left", padx=(8, 16))

        ttk.Radiobutton(
            options,
            text="Solid (5 photos)",
            variable=self.pipe_type_var,
            value="solid",
            command=self.refresh_preview,
        ).pack(side="left")

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(
            body,
            text="RN packages",
            padding=8,
        )

        right = ttk.LabelFrame(
            body,
            text="Selected evidence package",
            padding=8,
        )

        body.add(left, weight=1)
        body.add(right, weight=2)

        self.package_tree = ttk.Treeview(
            left,
            columns=("rn", "system", "section", "photos", "invert"),
            show="headings",
            height=18,
        )

        headers = {
            "rn": ("RN", 70),
            "system": ("System", 100),
            "section": ("Section", 180),
            "photos": ("Photos", 65),
            "invert": ("Invert", 65),
        }

        for column, (heading, width) in headers.items():
            self.package_tree.heading(column, text=heading)
            self.package_tree.column(column, width=width)

        self.package_tree.pack(fill="both", expand=True)
        self.package_tree.bind("<<TreeviewSelect>>", self.on_package_select)

        self.preview_tree = ttk.Treeview(
            right,
            columns=("slot", "milestone", "file"),
            show="headings",
        )

        self.preview_tree.heading("slot", text="Slot")
        self.preview_tree.heading("milestone", text="Milestone")
        self.preview_tree.heading("file", text="Selected file")

        self.preview_tree.column("slot", width=55)
        self.preview_tree.column("milestone", width=310)
        self.preview_tree.column("file", width=330)

        self.preview_tree.pack(fill="both", expand=True)

        self.invert_label = ttk.Label(
            right,
            text="Invert survey: -",
            wraplength=650,
        )
        self.invert_label.pack(anchor="w", pady=(8, 4))

        self.validation_label = ttk.Label(
            right,
            text="",
            wraplength=650,
        )
        self.validation_label.pack(anchor="w")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(10, 0))

        ttk.Button(
            buttons,
            text="Launch & Upload Selected Package",
            command=self.launch_upload,
        ).pack(side="right")

        ttk.Label(
            buttons,
            textvariable=self.status_var,
        ).pack(side="left")

    def scan_packages(self) -> None:
        try:
            root = Path(self.root_var.get().strip())
            self.packages = discover_packages(root)

            for item in self.package_tree.get_children():
                self.package_tree.delete(item)

            for index, package in enumerate(self.packages):
                self.package_tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        f"RN{package.rn}",
                        package.system_name,
                        package.section_name,
                        len(package.photos),
                        "Yes" if package.invert_pdf else "No",
                    ),
                )

            self.status_var.set(
                f"{len(self.packages)} RN package(s) found"
            )

        except Exception as exc:
            messagebox.showerror("Scan error", str(exc))

    def on_package_select(self, _event=None) -> None:
        selection = self.package_tree.selection()

        if not selection:
            return

        index = int(selection[0])
        self.selected_package = self.packages[index]
        self.refresh_preview()

    def refresh_preview(self) -> None:
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)

        package = self.selected_package

        if package is None:
            return

        pipe_type = self.pipe_type_var.get()
        slot_map = PHOTO_SLOT_MAP[pipe_type]

        for slot_number, milestone in slot_map.items():
            filename = (
                package.photos[slot_number - 1].name
                if len(package.photos) >= slot_number
                else "MISSING"
            )

            self.preview_tree.insert(
                "",
                "end",
                values=(slot_number, milestone, filename),
            )

        if pipe_type == "solid":
            self.preview_tree.insert(
                "",
                "end",
                values=(6, "Unused for solid pipe", "-"),
            )

        if package.invert_pdf:
            self.invert_label.config(
                text=f"Invert survey: {package.invert_pdf}"
            )
        else:
            self.invert_label.config(
                text="Invert survey: NOT FOUND"
            )

        valid, issues = validate_package(
            package,
            pipe_type,
        )

        if issues:
            self.validation_label.config(
                text=" | ".join(issues)
            )
        else:
            self.validation_label.config(
                text="Package is complete for upload."
            )

    def launch_upload(self) -> None:
        package = self.selected_package

        if package is None:
            messagebox.showwarning(
                "No package selected",
                "Select an RN package first.",
            )
            return

        pipe_type = self.pipe_type_var.get()

        valid, issues = validate_package(
            package,
            pipe_type,
        )

        if not valid:
            messagebox.showerror(
                "Incomplete evidence package",
                "\n".join(issues),
            )
            return

        required_count = len(PHOTO_SLOT_MAP[pipe_type])

        summary = [
            f"RN: RN{package.rn}",
            f"System: {package.system_name}",
            f"Section: {package.section_name}",
            f"Pipe type: {pipe_type}",
            "",
            "Photo upload order:",
        ]

        for slot, milestone in PHOTO_SLOT_MAP[pipe_type].items():
            summary.append(
                f"{slot}. {milestone} -> "
                f"{package.photos[slot - 1].name}"
            )

        if package.invert_pdf:
            summary.extend(
                [
                    "",
                    f"Invert survey -> {package.invert_pdf.name}",
                ]
            )
        else:
            summary.extend(
                [
                    "",
                    "Invert survey -> NOT FOUND (will be skipped)",
                ]
            )

        proceed = messagebox.askyesno(
            "Confirm upload package",
            "\n".join(summary)
            + "\n\nOpen Chrome and continue?",
        )

        if not proceed:
            return

        try:
            driver = start_chrome()
            driver.get(SYSTEMS_URL)

            messagebox.showinfo(
                "Open the correct checklist",
                (
                    "Chrome is open.\n\n"
                    "1. Sign in.\n"
                    "2. Navigate to Quality > QMS > Data Books and Inspections.\n"
                    "3. Open Inspection Requests.\n"
                    f"4. Open the Subsoil Drain Checklist for RN{package.rn}.\n\n"
                    "When the correct Checklist Capture Details page is visible, "
                    "click OK."
                ),
            )

            verify_correct_checklist(
                driver,
                package.rn,
            )

            self.status_var.set(
                f"Uploading RN{package.rn} photos..."
            )
            self.update_idletasks()

            upload_photo_package(
                driver,
                package,
                pipe_type,
            )

            if package.invert_pdf:
                self.status_var.set(
                    f"Uploading RN{package.rn} invert survey..."
                )
                self.update_idletasks()

                upload_invert_survey(
                    driver,
                    package,
                )

            self.status_var.set(
                f"RN{package.rn} evidence passed to webpage"
            )

            messagebox.showinfo(
                "Upload complete",
                (
                    f"RN{package.rn} evidence has been passed to the webpage.\n\n"
                    f"Photos uploaded: {required_count}\n"
                    f"Invert survey: "
                    f"{'uploaded' if package.invert_pdf else 'not found / skipped'}\n\n"
                    "Please visually verify all evidence before final submission."
                ),
            )

        except Exception as exc:
            self.status_var.set("Upload stopped")
            messagebox.showerror(
                "Upload stopped",
                str(exc),
            )


if __name__ == "__main__":
    app = SubsoilUploaderApp()
    app.mainloop()
