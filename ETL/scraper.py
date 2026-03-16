from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import os
import requests
from urllib.parse import urlparse


def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    return webdriver.Chrome(options=chrome_options)


def extract_links_from_page(driver):
    links = []
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "media-list--activity"))
        )
        media_items = driver.find_elements(By.CSS_SELECTOR, ".media.media--pers")
        for item in media_items:
            try:
                link_element = item.find_element(By.CSS_SELECTOR, "a.box-list__hyperlink")
                links.append(link_element.get_attribute("href"))
            except Exception as e:
                print(f"Error extracting link: {e}")
    except TimeoutException:
        print("Timeout waiting for page to load")
    return links


def has_next_page(driver):
    try:
        return driver.find_element(By.CSS_SELECTOR, "input.next").is_enabled()
    except NoSuchElementException:
        return False


def get_current_page_indicator(driver):
    try:
        return driver.find_element(By.CSS_SELECTOR, "span.page-link--custom.active").text.strip()
    except NoSuchElementException:
        return driver.current_url


def wait_for_page_change(driver, old_indicator, timeout=15):
    for _ in range(timeout):
        time.sleep(1)
        try:
            current = get_current_page_indicator(driver)
            if current and current != old_indicator:
                return True
        except Exception:
            continue
    print(f"Page did not change after {timeout} seconds")
    return False


def click_next_page(driver):
    try:
        old_indicator = get_current_page_indicator(driver)
        next_button = driver.find_element(By.CSS_SELECTOR, "input.next")

        if not next_button.is_enabled():
            return False

        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", next_button)
        time.sleep(1)

        for attempt in range(3):
            try:
                driver.execute_script("arguments[0].click();", next_button)
                if wait_for_page_change(driver, old_indicator):
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "media-list--activity"))
                    )
                    return True
                print(f"Click attempt {attempt + 1} failed, retrying...")
                time.sleep(2)
            except Exception as e:
                print(f"Click attempt {attempt + 1} error: {e}")
                time.sleep(2)

        print("All click attempts failed")
    except Exception as e:
        print(f"Error clicking next page: {e}")
    return False


def download_pdf_file(pdf_url, filepath):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(pdf_url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()

        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"Downloaded: {os.path.getsize(filepath)} bytes")
            return True

        print("Downloaded file is empty or missing")
        return False
    except Exception as e:
        print(f"Error downloading PDF: {e}")
        return False


def download_pdf_from_page(driver, regulation_url, download_folder="downloaded_pdfs"):
    try:
        driver.get(regulation_url)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "layout-flex__content"))
        )

        pdf_links = driver.find_elements(By.CSS_SELECTOR, ".layout-flex__content a[href$='.pdf']")
        if not pdf_links:
            print("No PDF links found")
            return None

        pdf_href = pdf_links[0].get_attribute("href")
        os.makedirs(download_folder, exist_ok=True)

        # Use original filename from URL
        parsed = urlparse(pdf_href)
        filename = os.path.basename(parsed.path)
        if not filename.endswith('.pdf'):
            filename = f"regulation_{int(time.time())}.pdf"

        filepath = os.path.join(download_folder, filename)
        print(f"Downloading: {filename}")

        return filepath if download_pdf_file(pdf_href, filepath) else None

    except TimeoutException:
        print(f"Timeout loading: {regulation_url}")
        return None
    except Exception as e:
        print(f"Error processing {regulation_url}: {e}")
        return None


def load_links_from_file(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            links = [line.strip() for line in f if line.strip()]
        print(f"Loaded {len(links)} links from {filename}")
        return links
    except FileNotFoundError:
        print(f"File not found: {filename}")
        return []
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        return []


def save_to_file(links, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        for link in links:
            f.write(link + '\n')


def save_download_report(downloaded_pdfs, filename="download_report.txt"):
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("PDF Download Report\n==================\n\n")
        for item in downloaded_pdfs:
            f.write(f"URL: {item['url']}\n")
            f.write(f"Downloaded: {'Yes' if item['downloaded'] else 'No'}\n")
            if item['pdf_path']:
                f.write(f"File: {item['pdf_path']}\n")
            f.write("-" * 50 + "\n")
    print(f"Report saved to {filename}")


def download_pdfs_from_links(links, download_folder="downloaded_pdfs"):
    driver = setup_driver()
    downloaded_pdfs = []

    try:
        print(f"Downloading PDFs for {len(links)} regulations...")
        for i, link in enumerate(links, 1):
            print(f"\n[{i}/{len(links)}] {link}")
            pdf_path = download_pdf_from_page(driver, link, download_folder)
            downloaded_pdfs.append({'url': link, 'pdf_path': pdf_path, 'downloaded': pdf_path is not None})
            time.sleep(2)
    except Exception as e:
        print(f"Error during downloads: {e}")
    finally:
        driver.quit()

    successful = sum(1 for item in downloaded_pdfs if item['downloaded'])
    print(f"\nDone: {successful}/{len(downloaded_pdfs)} PDFs downloaded")
    save_download_report(downloaded_pdfs)
    return downloaded_pdfs


def scrape_bi_regulations(base_url, output_file="bi_regulations.txt", max_pages=None, download_pdfs=False, download_folder="downloaded_pdfs"):
    driver = setup_driver()
    all_links = set()
    downloaded_pdfs = []
    page_number = 1

    try:
        print("Starting scrape...")
        driver.get(base_url)
        time.sleep(5)

        while True:
            print(f"Scraping page {page_number}...")
            page_links = extract_links_from_page(driver)

            new_count = sum(1 for link in page_links if link not in all_links)
            all_links.update(page_links)
            print(f"Found {len(page_links)} links ({new_count} new)")

            if download_pdfs:
                processed_urls = {item['url'] for item in downloaded_pdfs}
                to_download = [link for link in page_links if link not in processed_urls]
                for i, link in enumerate(to_download, 1):
                    total_so_far = len(downloaded_pdfs) + i
                    print(f"\n[PDF {i}/{len(to_download)} on page {page_number} | total: {total_so_far}] {link}")
                    pdf_path = download_pdf_from_page(driver, link, download_folder)
                    downloaded_pdfs.append({'url': link, 'pdf_path': pdf_path, 'downloaded': pdf_path is not None})
                    time.sleep(1)

            if max_pages and page_number >= max_pages:
                print(f"Reached page limit ({max_pages})")
                break

            if has_next_page(driver) and click_next_page(driver):
                page_number += 1
            else:
                print("No more pages")
                break

    except Exception as e:
        print(f"Error: {e}")
    finally:
        driver.quit()

    unique_links = sorted(all_links)
    if unique_links:
        save_to_file(unique_links, output_file)
        print(f"\nDone! {len(unique_links)} unique regulations across {page_number} pages → {output_file}")
        if download_pdfs:
            successful = sum(1 for item in downloaded_pdfs if item['downloaded'])
            print(f"Downloaded {successful}/{len(downloaded_pdfs)} PDFs")
            save_download_report(downloaded_pdfs)
    else:
        print("No links found!")

    return unique_links, downloaded_pdfs if download_pdfs else []