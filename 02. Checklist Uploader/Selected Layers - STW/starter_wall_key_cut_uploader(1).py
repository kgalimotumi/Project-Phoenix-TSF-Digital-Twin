from __future__ import annotations

import re
from pathlib import Path

DEPENDENCY_ERROR: ImportError | None = None

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        StaleElementReferenceException,
        TimeoutException,
    )
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError as exc:
    DEPENDENCY_ERROR = exc


# ---------------------------------------------------------------------------
# PROJECT SETTINGS
# ---------------------------------------------------------------------------

LAYER_ROOT = Path(
    r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop"
    r"\INREP36600 - Tweefontein TSF Project Files"
    r"\15 Quality\6. Inspections"
    r"\10. Inspection Photos (Selected Layers)"
    r"\Stater Wall - Key Cut"
)

SYSTEMS_URL = "https://systems.stefstocks.com/web/login"
WAIT_SECONDS = 30
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SECTION_PATTERN = re.compile(r"^Section\s*(\d+)\s*$", re.IGNORECASE)
LAYER_PATTERN = re.compile(r"^Layer\s*(\d+)\s*$", re.IGNORECASE)
RN_PATTERN = re.compile(r"(?<![A-Z0-9])RN\s*[-_ ]?\s*0*(\d+)(?!\d)", re.IGNORECASE)

HEADER_VALUES = {
    "Drawing Number": "23-2310-WME-104",
    "Revision Number": "1",
    "Area": "Tweefontein TSF Phase 3A",
    "Source of Material": "Samancor Waste Rock Stockpile",
    "Approval of Material": "Yes",
}


# ---------------------------------------------------------------------------
# LOCAL FOLDER SELECTION
# ---------------------------------------------------------------------------

def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def list_images(folder: Path) -> list[Path]:
    return sorted(
        (
            path for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: natural_key(path.name),
    )


def scan_gap_free_layers() -> tuple[list[tuple[int, int, Path, list[Path]]], list[str]]:
    if not LAYER_ROOT.exists():
        raise FileNotFoundError(f"Starter Wall folder was not found:\n{LAYER_ROOT}")

    ready: list[tuple[int, int, Path, list[Path]]] = []
    blocked: list[str] = []
    section_folders: list[tuple[int, Path]] = []

    for folder in LAYER_ROOT.iterdir():
        if folder.is_dir() and (match := SECTION_PATTERN.match(folder.name)):
            section_folders.append((int(match.group(1)), folder))

    if not section_folders:
        raise RuntimeError("No folders named Section 1, Section 2, etc. were found.")

    for section, section_folder in sorted(section_folders):
        layers: dict[int, tuple[Path, list[Path]]] = {}
        for folder in section_folder.iterdir():
            if folder.is_dir() and (match := LAYER_PATTERN.match(folder.name)):
                layer = int(match.group(1))
                layers[layer] = (folder, list_images(folder))

        expected = 1
        while expected in layers and layers[expected][1]:
            folder, images = layers[expected]
            ready.append((section, expected, folder, images))
            expected += 1

        for layer in sorted(layers):
            if layer >= expected:
                folder, images = layers[layer]
                reason = "empty folder" if not images else f"Layer {expected} is missing/empty"
                blocked.append(
                    f"Section {section}, Layer {layer}: blocked ({reason}); {len(images)} picture(s)"
                )

    return ready, blocked


def choose_layer() -> tuple[int, int, Path, list[Path]]:
    ready, blocked = scan_gap_free_layers()

    print("\n" + "=" * 78)
    print("STARTER WALL - KEY CUT: GAP-FREE LAYER FOLDERS")
    print("=" * 78)
    for number, (section, layer, _folder, images) in enumerate(ready, start=1):
        print(f"{number:>2}. Section {section} - Layer {layer}  [{len(images)} picture(s)]")

    if blocked:
        print("\nExcluded until the gaps are closed:")
        for item in blocked:
            print(f"  - {item}")

    if not ready:
        raise RuntimeError("No gap-free layer folders containing pictures are ready.")

    while True:
        value = input("\nEnter the number of the layer folder to upload: ").strip()
        try:
            return ready[int(value) - 1]
        except (ValueError, IndexError):
            print("Please enter a valid number from the list.")


def ask_for_rn() -> str:
    while True:
        value = input("Enter the manually booked RN (example RN156 or 156): ").strip().upper()
        match = RN_PATTERN.search(value if value.startswith("RN") else f"RN{value}")
        if match:
            return f"RN{int(match.group(1))}"
        print("Please enter a valid RN.")


# ---------------------------------------------------------------------------
# CHROME AND WEBSITE HELPERS
# ---------------------------------------------------------------------------

def start_chrome() -> webdriver.Chrome:
    if DEPENDENCY_ERROR is not None:
        raise RuntimeError(
            "Missing browser packages. Run this once in Command Prompt:\n\n"
            "python -m pip install selenium webdriver-manager"
        ) from DEPENDENCY_ERROR
    options = Options()
    options.add_experimental_option("detach", True)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.maximize_window()
    driver.get(SYSTEMS_URL)
    return driver


def scroll_to_element(driver: webdriver.Chrome, element) -> None:
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center', inline:'nearest'});",
        element,
    )


def wait_until_page_is_ready(driver: webdriver.Chrome) -> None:
    """Wait until the Stef Stocks busy image/overlay is no longer blocking clicks."""
    busy_selector = (
        "img.spinner, img[src*='busy_image'], .ngx-spinner-overlay, "
        ".loading-spinner, mat-spinner, .mat-mdc-progress-spinner, div.overlay"
    )
    def page_is_not_busy(current_driver) -> bool:
        try:
            return not any(
                element.is_displayed()
                for element in current_driver.find_elements(By.CSS_SELECTOR, busy_selector)
            )
        except StaleElementReferenceException:
            return False

    WebDriverWait(driver, WAIT_SECONDS).until(page_is_not_busy)


def exact_rn_is_visible(driver: webdriver.Chrome, rn: str) -> bool:
    searchable_text = [driver.find_element(By.TAG_NAME, "body").text]

    # Selenium's element.text excludes values displayed inside input controls.
    # The checklist RN is shown in the Description input, so include every
    # populated input/textarea value in the safety check.
    for field in driver.find_elements(By.CSS_SELECTOR, "input, textarea"):
        value = field.get_attribute("value")
        if value:
            searchable_text.append(value)

    page_text = re.sub(r"\s+", " ", " ".join(searchable_text)).upper()
    number = int(rn[2:])
    return re.search(
        rf"(?<![A-Z0-9])RN\s*[-_ ]?\s*0*{number}(?!\d)",
        page_text,
        re.IGNORECASE,
    ) is not None


# ---------------------------------------------------------------------------
# CHECKLIST HEADER AND ITEMS
# ---------------------------------------------------------------------------

def open_named_panel(driver: webdriver.Chrome, title_text: str):
    wait = WebDriverWait(driver, WAIT_SECONDS)
    title = wait.until(
        EC.presence_of_element_located((
            By.XPATH,
            f"//*[normalize-space()='{title_text}']",
        ))
    )
    panel = title.find_element(By.XPATH, "./ancestor::mat-expansion-panel[1]")
    header = panel.find_element(By.CSS_SELECTOR, "mat-expansion-panel-header")
    scroll_to_element(driver, header)
    if header.get_attribute("aria-expanded") != "true":
        wait.until(EC.element_to_be_clickable(header)).click()
    return panel


def find_item_row(panel, item_text: str, control_xpath: str):
    label = panel.find_element(
        By.XPATH,
        f".//*[normalize-space()={item_text!r}]",
    )
    return label.find_element(
        By.XPATH,
        f"./ancestor::*[{control_xpath}][1]",
    )


def find_live_checklist_panel(driver: webdriver.Chrome):
    """Return a fresh panel reference after Angular has rebuilt the checklist."""
    title = WebDriverWait(driver, WAIT_SECONDS).until(
        EC.presence_of_element_located((
            By.XPATH,
            "//*[normalize-space()='Checklist Items']",
        ))
    )
    return title.find_element(By.XPATH, "./ancestor::mat-expansion-panel[1]")


def set_angular_control_value(
    driver: webdriver.Chrome,
    field,
    value: str,
) -> None:
    """Set an Angular input without a physical click inside a scroll table."""
    driver.execute_script(
        """
        const element = arguments[0];
        const value = arguments[1];
        const prototype = element.tagName.toLowerCase() === 'textarea'
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
        setter.call(element, value);
        element.dispatchEvent(new Event('input', {bubbles: true}));
        element.dispatchEvent(new Event('change', {bubbles: true}));
        element.dispatchEvent(new Event('blur', {bubbles: true}));
        """,
        field,
        value,
    )
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda _: (field.get_attribute("value") or "").strip() == value
    )


def set_text_field(driver: webdriver.Chrome, panel, item_text: str, value: str) -> None:
    row = find_item_row(panel, item_text, ".//input or .//textarea")
    field = row.find_element(By.XPATH, ".//input | .//textarea")
    scroll_to_element(driver, field)
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda _: field.is_displayed() and field.is_enabled()
    )
    set_angular_control_value(driver, field, value)
    print(f"Header - {item_text}: {value}")


def complete_header(
    driver: webdriver.Chrome,
    rn: str,
    section: int,
    layer: int,
) -> None:
    panel = open_named_panel(driver, "Checklist Header/Info")
    values = dict(HEADER_VALUES)
    values["Section"] = f"{rn} STW Key cut Section {section} - Layer {layer}"
    for item_text in (
        "Drawing Number",
        "Revision Number",
        "Area",
        "Section",
        "Source of Material",
        "Approval of Material",
    ):
        set_text_field(driver, panel, item_text, values[item_text])


def answer_checklist_item(
    driver: webdriver.Chrome,
    item_text: str,
    answer: str,
    comment: str = "",
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)
    row = None

    for attempt in range(3):
        wait_until_page_is_ready(driver)
        try:
            # Each answer can rebuild the whole table, not only its row.
            # Always obtain a new panel, row and radio from the live DOM.
            panel = find_live_checklist_panel(driver)
            row = find_item_row(panel, item_text, ".//mat-radio-button")
            scroll_to_element(driver, row)
            radio = row.find_element(
                By.XPATH,
                f".//mat-radio-button[.//*[normalize-space()={answer!r}] or normalize-space()={answer!r}]",
            )
            radio_input = radio.find_element(By.CSS_SELECTOR, "input[type='radio']")
            if not radio_input.is_selected():
                wait.until(lambda _: radio.is_displayed() and radio.is_enabled())
                wait_until_page_is_ready(driver)
                # A save overlay can appear in the split-second between the
                # readiness check and a physical Selenium click. Trigger the
                # real Angular Material input directly so the overlay cannot
                # intercept the answer.
                driver.execute_script("arguments[0].click();", radio_input)
                wait.until(lambda _: radio_input.is_selected())
            break
        except (ElementClickInterceptedException, StaleElementReferenceException):
            if attempt == 2:
                raise

    if comment:
        wait_until_page_is_ready(driver)
        panel = find_live_checklist_panel(driver)
        row = find_item_row(panel, item_text, ".//mat-radio-button")
        fields = row.find_elements(By.XPATH, ".//textarea | .//input[not(@type='radio')]")
        if not fields:
            raise RuntimeError(f"Comments field not found for: {item_text}")
        field = fields[-1]
        set_angular_control_value(driver, field, comment)
    print(f"Checklist - {item_text}: {answer}" + (f" ({comment})" if comment else ""))


def complete_checklist_items(driver: webdriver.Chrome, section: int) -> None:
    open_named_panel(driver, "Checklist Items")
    thickness = "600mm" if section == 2 else "300mm"
    for item_text in (
        "Has your pre-inspection been done",
        "Selected layer",
        "Thickness",
        "Compaction Testing",
        "Levels",
        "Visual Inspection",
    ):
        answer_checklist_item(
            driver,
            item_text,
            "Yes",
            thickness if item_text == "Thickness" else "",
        )
    answer_checklist_item(driver, "Laboratory testing", "N/A")
    answer_checklist_item(driver, "Statistical control", "N/A")


def open_photos_panel(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, WAIT_SECONDS)
    title_xpath = "//*[normalize-space()='Sketches/Photos (Required)']"

    for attempt in range(3):
        wait_until_page_is_ready(driver)
        try:
            title = wait.until(EC.presence_of_element_located((By.XPATH, title_xpath)))
            panel = title.find_element(By.XPATH, "./ancestor::mat-expansion-panel[1]")
            header = panel.find_element(By.CSS_SELECTOR, "mat-expansion-panel-header")
            scroll_to_element(driver, header)
            wait_until_page_is_ready(driver)
            if header.get_attribute("aria-expanded") != "true":
                wait.until(EC.element_to_be_clickable(header)).click()
                wait.until(lambda _: header.get_attribute("aria-expanded") == "true")
            return panel
        except (ElementClickInterceptedException, StaleElementReferenceException):
            if attempt == 2:
                raise

    raise RuntimeError("The Sketches/Photos panel could not be opened.")


def available_photo_numbers(panel) -> list[int]:
    numbers: list[int] = []
    for heading in panel.find_elements(
        By.XPATH,
        ".//*[starts-with(normalize-space(), 'Sketch/Photo')]",
    ):
        match = re.fullmatch(
            r"\s*Sketch/Photo\s*(\d+)\s*",
            heading.text,
            re.IGNORECASE,
        )
        if match:
            numbers.append(int(match.group(1)))
    return sorted(set(numbers))


def get_photo_section(panel, photo_number: int):
    heading = panel.find_element(
        By.XPATH,
        f".//*[normalize-space()='Sketch/Photo {photo_number}']",
    )
    return heading.find_element(By.XPATH, "./ancestor::div[.//mat-select][1]")


def handle_replace_confirmation(driver: webdriver.Chrome) -> None:
    try:
        dialog = WebDriverWait(driver, 3).until(
            EC.visibility_of_element_located((
                By.XPATH,
                "//mat-dialog-container[.//*[contains(normalize-space(), 'Replace Sketch')]]",
            ))
        )
        dialog.find_element(
            By.XPATH,
            ".//button[.//*[normalize-space()='Yes'] or normalize-space()='Yes']",
        ).click()
        WebDriverWait(driver, WAIT_SECONDS).until(EC.invisibility_of_element(dialog))
    except TimeoutException:
        pass


def select_photo_type(driver: webdriver.Chrome, section, photo_number: int) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)
    selector = section.find_element(By.XPATH, ".//mat-select[1]")
    if selector.text.strip().lower() == "photo":
        return
    scroll_to_element(driver, selector)
    wait.until(lambda _: selector.is_displayed() and selector.is_enabled())
    selector.click()
    wait.until(
        EC.element_to_be_clickable((
            By.XPATH,
            "//mat-option[.//*[normalize-space()='Photo'] or normalize-space()='Photo']",
        ))
    ).click()
    handle_replace_confirmation(driver)
    wait.until(lambda _: "photo" in selector.text.strip().lower())
    print(f"Sketch/Photo {photo_number}: type set to Photo")


def upload_one_photo(
    driver: webdriver.Chrome,
    panel,
    photo_number: int,
    image_path: Path,
) -> None:
    section = get_photo_section(panel, photo_number)
    select_photo_type(driver, section, photo_number)

    # Angular may rebuild the section after changing the type.
    section = get_photo_section(panel, photo_number)
    file_input = WebDriverWait(driver, WAIT_SECONDS).until(
        lambda _: section.find_element(By.CSS_SELECTOR, "input[type='file']")
    )
    driver.execute_script(
        """
        arguments[0].removeAttribute('hidden');
        arguments[0].style.display='block';
        arguments[0].style.visibility='visible';
        arguments[0].style.opacity='1';
        """,
        file_input,
    )
    scroll_to_element(driver, file_input)
    file_input.send_keys(str(image_path.resolve()))
    print(f"Sketch/Photo {photo_number}: {image_path.name}")


def upload_folder_pictures(
    driver: webdriver.Chrome,
    section: int,
    images: list[Path],
) -> int:
    panel = open_photos_panel(driver)
    if not available_photo_numbers(panel):
        raise RuntimeError("No Sketch/Photo slots were found on this checklist.")

    section_image = LAYER_ROOT / f"S{section}.jpg"
    if not section_image.exists():
        raise FileNotFoundError(
            f"Photo 1 section image was not found: {section_image.name}\n{section_image}"
        )
    upload_images = [section_image, *images]

    used_slots: set[int] = set()
    upload_count = 0
    for image_path in upload_images:
        slots = [number for number in available_photo_numbers(panel) if number not in used_slots]
        if not slots:
            try:
                slots = WebDriverWait(driver, 3).until(
                    lambda _: [
                        number for number in available_photo_numbers(panel)
                        if number not in used_slots
                    ]
                )
            except TimeoutException:
                break
        photo_number = slots[0]
        upload_one_photo(driver, panel, photo_number, image_path)
        used_slots.add(photo_number)
        upload_count += 1

    if upload_count < len(upload_images):
        print(
            f"\nWARNING: Photo 1 plus the folder contain {len(upload_images)} pictures, "
            f"but the checklist exposed only {upload_count} photo slot(s). "
            f"{len(upload_images) - upload_count} picture(s) were not loaded."
        )
    return upload_count


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        section, layer, folder, images = choose_layer()
        rn = ask_for_rn()

        print("\nSelected upload:")
        print(f"  RN       : {rn}")
        print(f"  Reference: Starter Wall - Section {section} - Key Cut Layer {layer}")
        print(f"  Folder   : {folder}")
        for number, image in enumerate(images, start=1):
            print(f"  Photo {number:<2}: {image.name}")

        driver = start_chrome()
        print(
            "\nChrome is open. Complete these steps manually:\n"
            "1. Log in.\n"
            "2. Open Quality > QMS > Data Books and Inspections.\n"
            "3. Open Inspection Requests and select the contract.\n"
            f"4. Choose the booked {rn} checklist.\n"
            "5. Open its Checklist Capture Details page.\n"
        )
        input(f"When the correct {rn} checklist page is open, press ENTER here...")

        if not exact_rn_is_visible(driver, rn):
            raise RuntimeError(
                f"SAFETY STOP: {rn} is not visible on the current webpage. "
                "No pictures were uploaded."
            )

        confirmation = input(
            f"\nVerified {rn}. Type UPLOAD to load the Section {section}, "
            f"Layer {layer} checklist information and pictures: "
        ).strip().upper()
        if confirmation != "UPLOAD":
            print("Upload cancelled. No pictures were loaded.")
            return

        print("\n--- Completing Checklist Header/Info ---")
        complete_header(driver, rn, section, layer)

        print("\n--- Completing Checklist Items ---")
        complete_checklist_items(driver, section)

        print("\n--- Uploading Section and Layer Pictures ---")
        count = upload_folder_pictures(driver, section, images)
        print("\n" + "=" * 78)
        print(f"{count} picture(s) were loaded into the {rn} checklist page.")
        print("Review every preview, then save/submit the checklist manually.")
        print("=" * 78)

    except Exception as exc:
        print(f"\nERROR: {exc}")

    input("\nPress ENTER to close this program...")


if __name__ == "__main__":
    main()
