from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


# ---------------------------------------------------------------------------
# PROJECT PATHS
# ---------------------------------------------------------------------------

QUALITY_INSPECTIONS_FOLDER = Path(
    r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop"
    r"\INREP36600 - Tweefontein TSF Project Files"
    r"\15 Quality\6. Inspections"
)

MERGED_RESULTS_FOLDER = (
    QUALITY_INSPECTIONS_FOLDER
    / "3. TSF - Basin Shaping"
    / "Merged results"
)

CHECKLIST_PHOTO_1_FOLDER = (
    QUALITY_INSPECTIONS_FOLDER
    / "9. Checklist pic 1 (Earth)"
)

CHECKLIST_PHOTO_2_FOLDER = (
    QUALITY_INSPECTIONS_FOLDER
    / "10. Inspection Photos (Selected Layers)"
    / "Density"
    / "Checklist pic 2"
)

SYSTEMS_URL = "https://systems.stefstocks.com/web/login"

WAIT_SECONDS = 30

RN_PATTERN = re.compile(r"\bRN\s*[-_ ]?\s*0*(\d+)\b", re.IGNORECASE)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}


# ---------------------------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------------------------

def copy_to_clipboard(text: str) -> None:
    root = tk.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    root.destroy()


def extract_rn(value: str) -> str | None:
    match = RN_PATTERN.search(value)
    return str(int(match.group(1))) if match else None


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


# ---------------------------------------------------------------------------
# LOCAL FILE RESOLUTION
# ---------------------------------------------------------------------------

def choose_one_pdf() -> Path:
    pdfs = sorted(MERGED_RESULTS_FOLDER.glob("*.pdf"))

    if not pdfs:
        raise FileNotFoundError(
            f"No PDF files found in:\n{MERGED_RESULTS_FOLDER}"
        )

    print("\nMerged PDF files:\n")

    for number, pdf_path in enumerate(pdfs, start=1):
        rn = extract_rn(pdf_path.stem)
        rn_text = f"  [RN{rn}]" if rn else "  [NO RN FOUND]"
        print(f"{number}. {pdf_path.name}{rn_text}")

    while True:
        selection = input(
            "\nEnter the number of the PDF / RN package to upload: "
        ).strip()

        try:
            selected_number = int(selection)
            return pdfs[selected_number - 1]

        except (ValueError, IndexError):
            print("Please enter a valid number from the list.")


def list_images(folder: Path) -> list[Path]:
    if not folder.exists():
        raise FileNotFoundError(f"Photo folder does not exist:\n{folder}")

    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def find_image_for_rn(folder: Path, rn: str, label: str) -> Path:
    """
    Match an image by RN anywhere in the filename.

    Examples accepted:
        RN79.png
        RN 79.jpg
        RN079 - density.jpeg

    If more than one image matches the RN, an exact RN-only filename is
    preferred. If ambiguity remains, the script stops rather than uploading
    the wrong evidence.
    """
    matches = [
        path
        for path in list_images(folder)
        if extract_rn(path.stem) == rn
    ]

    if not matches:
        raise FileNotFoundError(
            f"{label} not found for RN{rn}.\n"
            f"Searched:\n{folder}"
        )

    exact_pattern = re.compile(
        rf"^\s*RN\s*[-_ ]?\s*0*{re.escape(rn)}\s*$",
        re.IGNORECASE,
    )

    exact_matches = [
        path for path in matches
        if exact_pattern.match(path.stem)
    ]

    if len(exact_matches) == 1:
        return exact_matches[0]

    if len(matches) == 1:
        return matches[0]

    choices = "\n".join(f"  - {path.name}" for path in matches)
    raise RuntimeError(
        f"More than one {label} matches RN{rn}.\n"
        f"Please keep only one intended checklist image for that RN, "
        f"or rename the intended image exactly RN{rn}.\n\n"
        f"Matches:\n{choices}"
    )


def resolve_rn_package(pdf_path: Path) -> tuple[str, Path, Path]:
    rn = extract_rn(pdf_path.stem)

    if not rn:
        raise RuntimeError(
            "The selected merged PDF filename does not contain an RN number.\n"
            f"File: {pdf_path.name}\n\n"
            "The uploader uses the RN to match Checklist Photo 1 and Photo 2."
        )

    photo_1 = find_image_for_rn(
        CHECKLIST_PHOTO_1_FOLDER,
        rn,
        "Checklist Photo 1 (Earth screenshot)",
    )

    photo_2 = find_image_for_rn(
        CHECKLIST_PHOTO_2_FOLDER,
        rn,
        "Checklist Photo 2 (inspection photo)",
    )

    return rn, photo_1, photo_2


def print_package_summary(
    rn: str,
    pdf_path: Path,
    photo_1: Path,
    photo_2: Path,
) -> None:
    print("\n" + "=" * 72)
    print(f"RN{rn} EVIDENCE PACKAGE")
    print("=" * 72)
    print(f"Photo 1 - Earth screenshot : {photo_1.name}")
    print(f"Photo 2 - Inspection photo : {photo_2.name}")
    print(f"Lab result PDF             : {pdf_path.name}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# SKETCHES / PHOTOS
# ---------------------------------------------------------------------------

def open_sketches_photos_panel(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, WAIT_SECONDS)

    title = wait.until(
        EC.presence_of_element_located((
            By.XPATH,
            "//*[normalize-space()='Sketches/Photos (Required)']"
        ))
    )

    panel = title.find_element(
        By.XPATH,
        "./ancestor::mat-expansion-panel[1]"
    )

    header = panel.find_element(
        By.CSS_SELECTOR,
        "mat-expansion-panel-header"
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
    """
    Locate the block headed 'Sketch/Photo 1', 'Sketch/Photo 2', etc.
    """
    wait = WebDriverWait(driver, WAIT_SECONDS)
    heading_text = f"Sketch/Photo {photo_number}"

    heading = wait.until(
        lambda _: panel.find_element(
            By.XPATH,
            f".//h3[normalize-space()='{heading_text}']"
        )
    )

    # The Angular template places the Type selector and file input after the h3.
    # Use the nearest container that contains both the heading and a Type mat-select.
    section = heading.find_element(
        By.XPATH,
        "./ancestor::div[.//mat-select][1]"
    )

    return section


def handle_replace_confirmation(driver: webdriver.Chrome) -> None:
    """
    Changing an existing Sketch to Photo may show:
        Replace Sketch
        Are you sure you want to replace the Sketch with a Photo?
    """
    try:
        short_wait = WebDriverWait(driver, 3)

        dialog = short_wait.until(
            EC.visibility_of_element_located((
                By.XPATH,
                "//mat-dialog-container["
                ".//*[contains(normalize-space(), 'Replace Sketch')]"
                "]"
            ))
        )

        yes_button = dialog.find_element(
            By.XPATH,
            ".//button[.//*[normalize-space()='Yes'] "
            "or normalize-space()='Yes']"
        )

        yes_button.click()

        WebDriverWait(driver, WAIT_SECONDS).until(
            EC.invisibility_of_element(dialog)
        )

        print("Confirmed replacement of Sketch with Photo.")

    except TimeoutException:
        # No confirmation is expected when changing from None to Photo.
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
        print(f"Sketch/Photo {photo_number}: Type is already Photo.")
        return

    scroll_to_element(driver, mat_select)
    wait.until(lambda _: mat_select.is_displayed() and mat_select.is_enabled())
    mat_select.click()

    photo_option = wait.until(
        EC.element_to_be_clickable((
            By.XPATH,
            "//mat-option["
            ".//*[normalize-space()='Photo'] "
            "or normalize-space()='Photo'"
            "]"
        ))
    )

    photo_option.click()
    handle_replace_confirmation(driver)

    wait.until(
        lambda _: "photo" in mat_select.text.strip().lower()
    )

    print(f"Sketch/Photo {photo_number}: Type set to Photo.")


def get_photo_file_input(
    driver: webdriver.Chrome,
    panel,
    photo_number: int,
):
    """
    Re-locate the section after Angular changes Type -> Photo, because the
    DOM below the selector can be re-rendered.
    """
    wait = WebDriverWait(driver, WAIT_SECONDS)

    section = get_photo_section(
        driver=driver,
        panel=panel,
        photo_number=photo_number,
    )

    file_input = wait.until(
        lambda _: section.find_element(
            By.CSS_SELECTOR,
            "input[type='file']"
        )
    )

    return file_input


def upload_checklist_photo(
    driver: webdriver.Chrome,
    panel,
    photo_number: int,
    image_path: Path,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

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

    # The page marks this input as hidden and normally opens the Gallery modal
    # through the Add Photo button. Removing the hidden attribute lets Selenium
    # send the chosen image directly to the same Angular file input.
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

    # Wait until the preview no longer says "No Photo uploaded", where possible.
    try:
        wait.until(
            lambda _: (
                "no photo uploaded"
                not in get_photo_section(
                    driver, panel, photo_number
                ).text.lower()
            )
        )
    except TimeoutException:
        # Some Angular implementations do not change the surrounding text,
        # even though the file input accepted the file. Continue and report it.
        pass

    print(
        f"Sketch/Photo {photo_number} file passed to webpage: "
        f"{image_path.name}"
    )


def upload_required_photos(
    driver: webdriver.Chrome,
    photo_1: Path,
    photo_2: Path,
) -> None:
    panel = open_sketches_photos_panel(driver)

    upload_checklist_photo(
        driver=driver,
        panel=panel,
        photo_number=1,
        image_path=photo_1,
    )

    upload_checklist_photo(
        driver=driver,
        panel=panel,
        photo_number=2,
        image_path=photo_2,
    )


# ---------------------------------------------------------------------------
# OPTIONAL SUPPORTING DOCUMENTS / LAB RESULT
# ---------------------------------------------------------------------------

def open_optional_supporting_documents(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, WAIT_SECONDS)

    title = wait.until(
        EC.presence_of_element_located((
            By.XPATH,
            "//*[normalize-space()='Optional Supporting Documents']"
        ))
    )

    panel = title.find_element(
        By.XPATH,
        "./ancestor::mat-expansion-panel[1]"
    )

    header = panel.find_element(
        By.CSS_SELECTOR,
        "mat-expansion-panel-header"
    )

    scroll_to_element(driver, header)

    if header.get_attribute("aria-expanded") != "true":
        wait.until(
            EC.element_to_be_clickable(header)
        ).click()

    wait.until(
        lambda _: panel.find_element(
            By.XPATH,
            ".//button[.//*[normalize-space()='Add']]"
        ).is_displayed()
    )

    return panel


def click_add_button(
    driver: webdriver.Chrome,
    panel,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    add_button = panel.find_element(
        By.XPATH,
        ".//button[.//*[normalize-space()='Add']]"
    )

    scroll_to_element(driver, add_button)

    wait.until(
        lambda _: add_button.is_displayed()
        and add_button.is_enabled()
    )

    add_button.click()


def get_supporting_document_dialog(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, WAIT_SECONDS)

    return wait.until(
        EC.visibility_of_element_located((
            By.XPATH,
            "//mat-dialog-container["
            ".//*[contains(normalize-space(), "
            "'Checklist Supporting Document')]"
            "]"
        ))
    )


def select_test_results(
    driver: webdriver.Chrome,
    dialog,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    document_type = wait.until(
        lambda _: dialog.find_element(
            By.XPATH,
            ".//mat-select"
        )
    )

    document_type.click()

    test_results = wait.until(
        EC.element_to_be_clickable((
            By.XPATH,
            "//mat-option["
            ".//*[normalize-space()='Test Results']"
            " or normalize-space()='Test Results'"
            "]"
        ))
    )

    test_results.click()


def enter_description(
    driver: webdriver.Chrome,
    dialog,
    description: str,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    description_field = wait.until(
        lambda _: dialog.find_element(
            By.XPATH,
            ".//mat-label[contains(normalize-space(), 'Description')]"
            "/ancestor::mat-form-field[1]"
            "//input"
            " | "
            ".//mat-label[contains(normalize-space(), 'Description')]"
            "/ancestor::mat-form-field[1]"
            "//textarea"
        )
    )

    scroll_to_element(driver, description_field)

    wait.until(
        lambda _: (
            description_field.is_displayed()
            and description_field.is_enabled()
        )
    )

    description_field.click()
    description_field.send_keys(Keys.CONTROL, "a")
    description_field.send_keys(description)
    description_field.send_keys(Keys.TAB)

    wait.until(
        lambda _: description_field.get_attribute("value").strip()
        == description
    )

    print(f"Description entered: {description}")


def click_select_document_and_save(
    driver: webdriver.Chrome,
    dialog,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    save_button = wait.until(
        lambda _: dialog.find_element(
            By.XPATH,
            ".//button["
            ".//*[contains(normalize-space(), "
            "'Select Document and Save')]"
            "]"
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

    print("Select Document and Save clicked.")


def upload_supporting_document_file(
    driver: webdriver.Chrome,
    pdf_path: Path,
) -> None:
    wait = WebDriverWait(driver, WAIT_SECONDS)

    # The document picker is opened after 'Select Document and Save'.
    # Prefer the most recently displayed/usable file input.
    inputs = wait.until(
        lambda _: driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='file']"
        )
    )

    if not inputs:
        raise RuntimeError("No file input was found for the supporting document.")

    file_input = inputs[-1]

    driver.execute_script(
        """
        arguments[0].removeAttribute('hidden');
        arguments[0].style.display = 'block';
        """,
        file_input,
    )

    file_input.send_keys(str(pdf_path.resolve()))


def upload_one_pdf(
    driver: webdriver.Chrome,
    pdf_path: Path,
) -> None:
    description = pdf_path.stem

    print(f"\nUploading lab result: {pdf_path.name}")
    print(f"Description: {description}")

    panel = open_optional_supporting_documents(driver)

    click_add_button(
        driver=driver,
        panel=panel,
    )

    dialog = get_supporting_document_dialog(driver)

    select_test_results(
        driver=driver,
        dialog=dialog,
    )

    enter_description(
        driver=driver,
        dialog=dialog,
        description=description,
    )

    click_select_document_and_save(
        driver=driver,
        dialog=dialog,
    )

    upload_supporting_document_file(
        driver=driver,
        pdf_path=pdf_path,
    )

    print("Lab-result PDF was passed to the webpage.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    selected_pdf = choose_one_pdf()

    rn, photo_1, photo_2 = resolve_rn_package(selected_pdf)

    print_package_summary(
        rn=rn,
        pdf_path=selected_pdf,
        photo_1=photo_1,
        photo_2=photo_2,
    )

    description = selected_pdf.stem
    copy_to_clipboard(description)

    print(
        "\nThe package has been validated locally."
        "\nNo upload has happened yet."
    )

    driver = start_chrome()
    driver.get(SYSTEMS_URL)

    print(
        "\nIn Chrome:\n"
        "1. Sign in.\n"
        "2. Open Quality > QMS.\n"
        "3. Open Data Books and Inspections.\n"
        "4. Open Inspection Requests.\n"
        "5. Select the contract.\n"
        f"6. Open the linked checklist for RN{rn}.\n"
        "\nThe script will then attempt, in this order:\n"
        "   A. Sketch/Photo 1 = Earth screenshot\n"
        "   B. Sketch/Photo 2 = inspection photo\n"
        "   C. Optional Supporting Document = lab-result PDF\n"
    )

    input(
        f"When the correct RN{rn} Checklist Capture Details page is open, "
        "press ENTER here..."
    )

    print("\n--- Uploading required checklist photos ---")
    upload_required_photos(
        driver=driver,
        photo_1=photo_1,
        photo_2=photo_2,
    )

    print("\n--- Uploading laboratory result ---")
    upload_one_pdf(
        driver=driver,
        pdf_path=selected_pdf,
    )

    print("\n" + "=" * 72)
    print(f"RN{rn} package passed to the webpage.")
    print("Please visually verify Photo 1, Photo 2 and Test Results before final submission.")
    print("=" * 72)

    input("\nPress ENTER to finish the program...")


if __name__ == "__main__":
    main()
