"""Tool that calls Selenium."""
import json
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import validators
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from selenium import webdriver
from selenium.common.exceptions import (
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from chromegpt.tools.utils import (
    find_parent_element_text,
    get_all_text_elements,
    prettify_text,
    truncate_string_from_last_occurrence,
)


class SeleniumWrapper:
    """Wrapper around Selenium.

    To use, you should have the ``selenium`` python package installed.

    Example:
        .. code-block:: python

            from langchain import SeleniumWrapper
            selenium = SeleniumWrapper()
    """

    def __init__(self, headless: bool = False, docker: bool = True) -> None:
        """Initialize Selenium and start interactive session."""
        chrome_options = Options()
        if headless:
            chrome_options.add_argument("--headless")
        else:
            chrome_options.add_argument("--start-maximized")
        
        # Add options for CI environments
        if not docker:
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-extensions")
        
        if docker:
            self.driver = webdriver.Remote(
                "http://selenium-chrome:4444/wd/hub",
                options=chrome_options,
            )
        else:
            self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.implicitly_wait(5)  # Wait 5 seconds for elements to load

    def __del__(self) -> None:
        """Close Selenium session."""
        if hasattr(self, "driver") and self.driver is not None:
            self.driver.close()
            self.driver.quit()

    def previous_webpage(self) -> str:
        """Go back in browser history."""
        self.driver.back()
        return self.describe_website()

    def google_search(self, query: str) -> str:
        safe_string = urllib.parse.quote_plus(query)
        url = "https://www.google.com/search?q=" + safe_string
        # Go to website
        try:
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.driver.get(url)
        except Exception:
            return f"Cannot load website {url}. Try again later."

        # Scrape search results
        results = self._get_google_search_results()
        return (
            "Which url would you like to goto? Provide the full url starting with http"
            " or https to goto: "
            + json.dumps(results)
        )

    def _get_google_search_results(self) -> List[Dict[str, Any]]:
        # Scrape search results
        results = []
        page_source = self.driver.page_source
        soup = BeautifulSoup(page_source, "html.parser")
        search_results = soup.find_all("div", class_="g")
        for _, result in enumerate(search_results, start=1):
            if result.find("a") and result.find("h3"):
                title_element = result.find("h3")
                link_element = result.find("a")

                title = title_element.get_text()
                link = link_element.get("href")
                if title and link:
                    results.append(
                        {
                            "title": title,
                            "link": link,
                        }
                    )
        return results

    def describe_website(self, url: Optional[str] = None) -> str:
        """Describe the website."""
        output = ""
        if url:
            try:
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self.driver.get(url)
            except Exception:
                return (
                    f"Cannot load website {url}. Make sure you input the correct and"
                    " complete url starting with http:// or https://."
                )

        # Let driver wait for website to load
        time.sleep(1)  # Wait for website to load

        try:
            # Extract main content
            main_content = self._get_website_main_content()
        except WebDriverException:
            return "Website still loading, please wait a few seconds and try again."
        if main_content:
            output += f"{main_content}\n"

        # Extract interactable components (buttons and links)
        interactable_content = self._get_interactable_elements()
        if interactable_content:
            output += f"{interactable_content}\n"

        # Extract form inputs
        form_fields = self._find_form_fields()
        if form_fields:
            output += (
                "You can input text in these fields using fill_form function: "
                + form_fields
            )
        return output

    def click_button_by_text(self, button_text: str) -> str:
        # check if the button text is url
        if validators.url(button_text):
            return self.describe_website(button_text)
        # If it is google search, then fetch link from google
        if self.driver.current_url.startswith("https://www.google.com/search"):
            google_search_results = self._get_google_search_results()
            for result in google_search_results:
                if button_text.lower() in result["title"].lower():
                    return self.describe_website(result["link"])
        self.driver.switch_to.window(self.driver.window_handles[-1])
        # If there are string surrounded by double quotes, extract them
        if button_text.count('"') > 1:
            try:
                button_text = re.findall(r'"([^"]*)"', button_text)[0]
            except IndexError:
                # No text surrounded by double quotes
                pass
        try:
            elements = self.driver.find_elements(
                By.XPATH,
                "//button | //div[@role='button'] | //a | //input[@type='checkbox']",
            )

            if not elements:
                return (
                    "No interactable buttons found in the website. Try another website."
                )

            selected_element = None
            all_buttons = []
            for element in elements:
                text = find_parent_element_text(element)
                button_text = prettify_text(button_text)
                if (
                    element.is_displayed()
                    and element.is_enabled()
                    and (
                        text == button_text
                        or (
                            button_text in text
                            and abs(len(text) - len(button_text)) < 50
                        )
                    )
                ):
                    selected_element = element
                    if text and text not in all_buttons:
                        all_buttons.append(text)
                    break
            if not selected_element:
                return (
                    f"No interactable element found with text: {button_text}. Double"
                    " check the button text and try again. Available buttons:"
                    f" {json.dumps(all_buttons)}"
                )

            # Scroll the element into view and Click the element using JavaScript
            before_content = self.describe_website()
            actions = ActionChains(self.driver)
            actions.move_to_element(selected_element).click().perform()
            after_content = self.describe_website()
            if before_content == after_content:
                output = (
                    "Clicked interactable element but nothing changed on the website."
                )
            else:
                output = "Clicked interactable element and the website changed. Now "
                output += self.describe_website()
            return output
        except WebDriverException as e:
            return f"Error clicking button with text '{button_text}', message: {e.msg}"

    def find_form_inputs(self, url: Optional[str] = None) -> str:
        """Find form inputs on the website."""
        fields = self._find_form_fields(url)
        if fields:
            form_inputs = "Available Form Input Fields: " + fields
        else:
            form_inputs = "No form inputs found on current page. Try another website."
        return form_inputs

    def _find_form_fields(self, url: Optional[str] = None) -> str:
        """Find form fields on the website."""
        if url and url != self.driver.current_url and url.startswith("http"):
            try:
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self.driver.get(url)
                # Let driver wait for website to load
                time.sleep(1)  # Wait for website to load
            except WebDriverException as e:
                return f"Error loading url {url}, message: {e.msg}"
        fields = []
        for element in self.driver.find_elements(By.XPATH, "//textarea | //input"):
            label_txt = (
                element.get_attribute("name")
                or element.get_attribute("aria-label")
                or find_parent_element_text(element)
            )
            if (
                label_txt
                and "\n" not in label_txt
                and len(label_txt) < 100
                and label_txt not in fields
            ):
                label_txt = prettify_text(label_txt)
                fields.append(label_txt)
        return str(fields)

    def fill_out_form(self, form_input: Optional[str] = None, **kwargs: Any) -> str:
        """fill out form by form field name and input name"""
        filled_element = None
        if form_input and isinstance(form_input, str):
            # Clean up form input
            form_input_str = truncate_string_from_last_occurrence(
                string=form_input, character="}"  # type: ignore
            )
            try:
                form_input = json.loads(form_input_str)
            except json.decoder.JSONDecodeError:
                return (
                    "Invalid JSON input. Please check your input is JSON format and try"
                    " again. Make sure to use double quotes for strings. Example input:"
                    ' {"email": "foo@bar.com","name": "foo bar"}'
                )
        elif not form_input:
            form_input = kwargs  # type: ignore
        try:
            for element in self.driver.find_elements(By.XPATH, "//textarea | //input"):
                label_txt = (
                    element.get_attribute("name")
                    or element.get_attribute("aria-label")
                    or find_parent_element_text(element)
                )
                if label_txt:
                    label_txt = prettify_text(label_txt)
                    for key in form_input.keys():  # type: ignore
                        if prettify_text(key) == label_txt:
                            # Scroll the element into view
                            self.driver.execute_script(
                                "arguments[0].scrollIntoView();", element
                            )
                            time.sleep(0.5)  # Allow some time for the page to settle
                            try:
                                # Try clearing the input field
                                element.send_keys(Keys.CONTROL + "a")
                                element.send_keys(Keys.DELETE)
                                element.clear()
                            except WebDriverException:
                                pass
                            element.send_keys(form_input[key])  # type: ignore
                            filled_element = element
                            break
            if not filled_element:
                return (
                    f"Cannot find form with input: {form_input.keys()}."  # type: ignore
                    f" Available form inputs: {self._find_form_fields()}"
                )
            before_content = self.describe_website()
            filled_element.send_keys(Keys.RETURN)
            after_content = self.describe_website()
            if before_content != after_content:
                return (
                    f"Successfully filled out form with input: {form_input}, website"
                    f" changed after filling out form. Now {after_content}"
                )
            else:
                return (
                    f"Successfully filled out form with input: {form_input}, but"
                    " website did not change after filling out form."
                )
        except WebDriverException as e:
            # print(e)
            return f"Error filling out form with input {form_input}, message: {e.msg}"

    def scroll(self, direction: str) -> str:
        # Get the height of the current window
        window_height = self.driver.execute_script("return window.innerHeight")
        if direction == "up":
            window_height = -window_height

        # Scroll by 1 window height
        self.driver.execute_script(f"window.scrollBy(0, {window_height})")

        return self.describe_website()

    def _get_website_main_content(self) -> str:
        texts = get_all_text_elements(self.driver)
        pretty_texts = [prettify_text(text) for text in texts]
        if not pretty_texts:
            return ""

        description = (
            "Current window displays the following contents, try scrolling up or down"
            " to view more: "
        )
        description += json.dumps(pretty_texts)

        return description

    def _get_interactable_elements(self) -> str:
        # Extract interactable components (buttons and links)
        interactable_elements = self.driver.find_elements(
            By.XPATH,
            "//button | //div[@role='button'] | //a | //input[@type='checkbox']",
        )

        interactable_texts = []
        for element in interactable_elements:
            button_text = find_parent_element_text(element)
            button_text = prettify_text(button_text, 50)
            if (
                button_text
                and button_text not in interactable_texts
                and element.is_displayed()
                and element.is_enabled()
            ):
                interactable_texts.append(button_text)

        # Split up the links and the buttons
        buttons_text = []
        links_text = []
        for text in interactable_texts:
            if validators.url(text):
                links_text.append(text)
            else:
                buttons_text.append(text)
        interactable_output = ""
        if links_text:
            interactable_output += f"Goto these links: {json.dumps(links_text)}\n"
        if buttons_text:
            interactable_output += f"Click on these buttons: {json.dumps(buttons_text)}"
        return interactable_output

    def get_element_selectors(self, element_text: str) -> str:
        """Get CSS selectors and XPath for an element by text content."""
        try:
            elements = self.driver.find_elements(
                By.XPATH,
                "//button | //div[@role='button'] | //a | //input | "
                "//textarea | //select",
            )
            
            for element in elements:
                text = find_parent_element_text(element)
                if (
                    element.is_displayed()
                    and element.is_enabled()
                    and element_text.lower() in text.lower()
                ):
                    # Get various selector options
                    element_info = {
                        "text": text,
                        "tag_name": element.tag_name,
                        "id": element.get_attribute("id") or "",
                        "class": element.get_attribute("class") or "",
                        "name": element.get_attribute("name") or "",
                        "type": element.get_attribute("type") or "",
                        "role": element.get_attribute("role") or "",
                        "aria_label": element.get_attribute("aria-label") or "",
                    }
                    
                    # Generate CSS selectors
                    css_selectors = []
                    xpath_selectors = []
                    
                    # ID selector (most reliable)
                    if element_info["id"]:
                        css_selectors.append(f"#{element_info['id']}")
                        xpath_selectors.append(f"//*[@id='{element_info['id']}']")
                    
                    # Class selector
                    if element_info["class"]:
                        classes = element_info["class"].split()
                        if classes:
                            css_selectors.append(f".{'.'.join(classes)}")
                    
                    # Name attribute
                    if element_info["name"]:
                        css_selectors.append(f"[name='{element_info['name']}']")
                        xpath_selectors.append(f"//*[@name='{element_info['name']}']")
                    
                    # Text-based XPath
                    if text:
                        text_short = text[:30]
                        tag_name = element_info['tag_name']
                        xpath_selectors.append(
                            f"//{tag_name}[contains(text(), '{text_short}')]"
                        )
                        xpath_selectors.append(f"//*[contains(text(), '{text_short}')]")
                    
                    # Type-specific selectors
                    if element_info["type"]:
                        css_selectors.append(f"input[type='{element_info['type']}']")
                    
                    # Role-based
                    if element_info["role"]:
                        css_selectors.append(f"[role='{element_info['role']}']")
                        xpath_selectors.append(f"//*[@role='{element_info['role']}']")
                    
                    return json.dumps({
                        "element_info": element_info,
                        "css_selectors": css_selectors,
                        "xpath_selectors": xpath_selectors,
                        "recommended_css": css_selectors[0] if css_selectors else None,
                        "recommended_xpath": (
                            xpath_selectors[0] if xpath_selectors else None
                        )
                    }, indent=2)
            
            return f"No element found matching text: '{element_text}'"
            
        except WebDriverException as e:
            return f"Error finding element selectors: {e.msg}"

    def generate_cypress_test(
        self, test_name: str = "Generated Test", base_url: Optional[str] = None
    ) -> str:
        """Generate a Cypress test based on the current page and available elements."""
        current_url = self.driver.current_url
        if base_url is None:
            base_url = current_url
            
        # Get all interactable elements with detailed information
        elements = self.driver.find_elements(
            By.XPATH,
            "//button | //div[@role='button'] | //a | //input | //textarea | //select",
        )
        
        element_data = []
        for element in elements:
            if element.is_displayed() and element.is_enabled():
                text = find_parent_element_text(element)
                if text and len(text.strip()) > 0:
                    element_info = {
                        "text": text[:50],
                        "tag": element.tag_name,
                        "id": element.get_attribute("id") or "",
                        "class": element.get_attribute("class") or "",
                        "name": element.get_attribute("name") or "",
                        "type": element.get_attribute("type") or "",
                        "role": element.get_attribute("role") or "",
                    }
                    
                    # Generate selector for Cypress
                    selector = None
                    if element_info["id"]:
                        selector = f"#{element_info['id']}"
                    elif element_info["name"]:
                        selector = f"[name='{element_info['name']}']"
                    elif element_info["role"]:
                        selector = f"[role='{element_info['role']}']"
                    elif element_info["class"]:
                        classes = element_info["class"].split()
                        if classes:
                            selector = f".{classes[0]}"
                    
                    if selector:
                        element_info["selector"] = selector
                        element_data.append(element_info)
        
        # Generate Cypress test code
        cypress_test = f'''describe('{test_name}', () => {{
  beforeEach(() => {{
    cy.visit('{current_url}')
  }})

  it('should load the page successfully', () => {{
    cy.url().should('contain', '{current_url.split("://")[1].split("/")[0]}')
    cy.title().should('not.be.empty')
  }})

  // Available interactive elements for testing:
'''
        
        for i, element in enumerate(element_data[:10], 1):  # Limit to first 10 elements
            cypress_test += f'''
  it('should interact with element {i}: {element["text"][:30]}', () => {{
    // Element: {element["tag"]} - "{element["text"]}"
    // Selector: {element["selector"]}
    
    cy.get('{element["selector"]}').should('be.visible')
    
    // Uncomment based on element type:
    '''
            
            if element["tag"] in ["button", "a"] or element["role"] == "button":
                cypress_test += f'''// cy.get('{element["selector"]}').click()
    '''
            elif element["tag"] in ["input", "textarea"]:
                if element["type"] in ["text", "email", "password", ""]:
                    cypress_test += (
                        f"// cy.get('{element['selector']}').type('test input')\n"
                    )
                elif element["type"] == "checkbox":
                    cypress_test += (
                        f"// cy.get('{element['selector']}').check()\n"
                    )
            elif element["tag"] == "select":
                cypress_test += (
                    f"// cy.get('{element['selector']}').select('option-value')\n"
                )
            
            cypress_test += "  })\n"
        
        cypress_test += '''
  // Form filling example (customize as needed):
  it('should fill out forms', () => {
    // Add your form interaction tests here
    // Example:
    // cy.get('[name="email"]').type('test@example.com')
    // cy.get('[name="password"]').type('password123')
    // cy.get('button[type="submit"]').click()
  })
})'''
        
        return cypress_test

    def extract_page_elements_for_testing(self) -> str:
        """Extract comprehensive information about page elements for test automation."""
        try:
            # Get page information
            page_info = {
                "url": self.driver.current_url,
                "title": self.driver.title,
                "elements": []
            }
            
            # Find all potentially testable elements
            all_elements = self.driver.find_elements(By.XPATH, "//*")
            
            testable_elements = []
            for element in all_elements:
                if (element.is_displayed() and 
                    element.tag_name in [
                        "button", "a", "input", "textarea", "select", "div", "span"
                    ] and
                    (element.get_attribute("onclick") or 
                     element.get_attribute("role") in ["button", "link"] or
                     element.tag_name in [
                         "button", "a", "input", "textarea", "select"
                     ])):
                    
                    parent_text = find_parent_element_text(element)
                    element_data = {
                        "tag": element.tag_name,
                        "text": parent_text[:100] if parent_text else "",
                        "id": element.get_attribute("id") or "",
                        "class": element.get_attribute("class") or "",
                        "name": element.get_attribute("name") or "",
                        "type": element.get_attribute("type") or "",
                        "role": element.get_attribute("role") or "",
                        "href": element.get_attribute("href") or "",
                        "onclick": bool(element.get_attribute("onclick")),
                        "location": element.location,
                        "size": element.size
                    }
                    
                    # Generate multiple selector options
                    selectors = []
                    if element_data["id"]:
                        selectors.append({
                            "type": "id",
                            "selector": f"#{element_data['id']}",
                            "reliability": "high"
                        })
                    if element_data["name"]:
                        selectors.append({
                            "type": "name",
                            "selector": f"[name='{element_data['name']}']",
                            "reliability": "high"
                        })
                    if element_data["class"]:
                        # Use first 2 classes
                        classes = element_data["class"].split()[:2]
                        if classes:
                            selectors.append({
                                "type": "class",
                                "selector": f".{'.'.join(classes)}",
                                "reliability": "medium"
                            })
                    if element_data["text"]:
                        selectors.append({
                            "type": "text",
                            "selector": f":contains('{element_data['text'][:20]}')",
                            "reliability": "medium"
                        })
                    
                    element_data["selectors"] = selectors
                    testable_elements.append(element_data)
            
            page_info["elements"] = testable_elements[:20]  # Limit to first 20 elements
            
            return json.dumps(page_info, indent=2)
            
        except WebDriverException as e:
            return f"Error extracting page elements: {e.msg}"


class GoogleSearchInput(BaseModel):
    """Google search input model."""

    query: str = Field(..., description="search query")


class DescribeWebsiteInput(BaseModel):
    """Describe website input model."""

    url: str = Field(
        ...,
        description="full URL starting with http or https",
        example="https://www.google.com/",
    )


class ClickButtonInput(BaseModel):
    """Click button input model."""

    button_text: str = Field(
        ...,
        description="text of the button/link you want to click",
        example="Contact Us",
    )


class FindFormInput(BaseModel):
    """Find form input input model."""

    url: Optional[str] = Field(
        default=None,
        description="the current website url",
        example="https://www.google.com/",
    )


class FillOutFormInput(BaseModel):
    """Fill out form input model."""

    form_input: Optional[str] = Field(
        default=None,
        description="json formatted string with the input fields and their values",
        example='{"email": "foo@bar.com","name": "foo bar"}',
    )


class ScrollInput(BaseModel):
    """Scroll window."""

    direction: str = Field(
        default="down", description="direction to scroll, either 'up' or 'down'"
    )


class ElementSelectorsInput(BaseModel):
    """Get element selectors input model."""

    element_text: str = Field(
        ...,
        description="text content of the element to find selectors for",
        example="Login"
    )


class CypressTestInput(BaseModel):
    """Generate Cypress test input model."""

    test_name: str = Field(
        default="Generated Test",
        description="name for the generated test",
        example="User Login Flow"
    )
    base_url: Optional[str] = Field(
        default=None,
        description="base URL for the test (uses current URL if not provided)",
        example="https://example.com"
    )
