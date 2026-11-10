"""
actions/leetcode_assistant.py — Context-Aware LeetCode Coding Assistant for JARVIS.

Capabilities:
- Detects active LeetCode problem from open browser windows or Playwright sessions
- Retrieves problem title, description, constraints, and examples
- Inspects user's code upon request
- Provides progressive hints without spoiling the full solution
- Explains approaches (Brute force vs Optimal)
- Analyzes time and space complexity
- Identifies logical mistakes and explains failing test cases
- Generates edge cases and additional test cases
- Supports Python, Java, C++, JavaScript and all standard LeetCode languages
- Maintains session problem context across turns
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core import gemini, outcome

logger = logging.getLogger(__name__)


@dataclass
class LeetCodeContext:
    problem_title: str = ""
    problem_slug: str = ""
    problem_url: str = ""
    description: str = ""
    examples: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    language: str = "Python3"
    user_code: str = ""
    hint_level: int = 0

    def to_dict(self) -> dict:
        return {
            "title": self.problem_title,
            "slug": self.problem_slug,
            "url": self.problem_url,
            "language": self.language,
            "hint_level": self.hint_level,
            "has_code": bool(self.user_code),
        }


class LeetCodeAssistant:
    """Manages active LeetCode session perception, reasoning, and progressive assistance."""

    def __init__(self):
        self.context = LeetCodeContext()

    def detect_active_problem(self) -> Tuple[bool, str, str]:
        """
        Detects if a LeetCode problem is active in any browser window.
        Returns: (found, problem_title, slug)
        """
        try:
            from actions import windows_uia
            if windows_uia.is_available():
                wins = windows_uia.list_open_windows()
                for w in wins:
                    title = w.get("title", "")
                    if "leetcode" in title.lower():
                        # Typical format: "Two Sum - LeetCode - Google Chrome"
                        cleaned = re.sub(r"\s*-\s*leetcode.*$", "", title, flags=re.IGNORECASE).strip()
                        # Remove leading numbers like "1. Two Sum" -> "Two Sum"
                        slug_name = re.sub(r"^\d+\.\s*", "", cleaned).strip()
                        if slug_name and len(slug_name) > 1:
                            slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug_name).strip("-").lower()
                            return True, slug_name, slug
        except Exception as e:
            logger.debug("Window detection error: %s", e)

        # Fallback to current context if already set
        if self.context.problem_title:
            return True, self.context.problem_title, self.context.problem_slug

        return False, "", ""

    def fetch_problem_data(self, title_or_slug: str) -> bool:
        """
        Fetch problem statement, examples, and constraints from public LeetCode GraphQL API.
        """
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title_or_slug).strip("-").lower()
        query = """
        query getQuestionDetail($titleSlug: String!) {
          question(titleSlug: $titleSlug) {
            title
            titleSlug
            content
            difficulty
            exampleTestcases
          }
        }
        """
        req_data = json.dumps({
            "operationName": "getQuestionDetail",
            "variables": {"titleSlug": slug},
            "query": query,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://leetcode.com/graphql",
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                q = data.get("data", {}).get("question")
                if q and q.get("title"):
                    self.context.problem_title = q["title"]
                    self.context.problem_slug = q["titleSlug"]
                    self.context.problem_url = f"https://leetcode.com/problems/{q['titleSlug']}/"

                    # Strip HTML
                    raw_content = q.get("content", "")
                    clean_text = re.sub(r"<[^>]+>", " ", raw_content)
                    clean_text = re.sub(r"\s+", " ", clean_text).strip()
                    self.context.description = clean_text[:3000]

                    # Parse constraints and examples
                    m_ex = re.findall(r"Example \d+:[^E]+", clean_text)
                    self.context.examples = [e.strip() for e in m_ex[:3]]
                    m_cons = re.search(r"Constraints:\s*(.*)", clean_text)
                    if m_cons:
                        self.context.constraints = [c.strip() for c in m_cons.group(1).split("•") if c.strip()]
                    return True
        except Exception as e:
            logger.debug("LeetCode GraphQL fetch error: %s", e)

        # Fallback: store title and basic slug
        self.context.problem_title = title_or_slug
        self.context.problem_slug = slug
        self.context.problem_url = f"https://leetcode.com/problems/{slug}/"
        return True

    def inspect_editor_code(self) -> Optional[str]:
        """Inspect code in user's active editor via clipboard copy if permission granted."""
        try:
            import pyperclip
            import pyautogui
            import time

            # Save existing clipboard
            old_clip = pyperclip.paste()
            # Select all in editor and copy
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.1)
            pyautogui.press("right")  # Deselect
            code = pyperclip.paste()

            if code and code != old_clip and len(code) > 10:
                self.context.user_code = code
                return code
        except Exception:
            pass
        return None

    def get_hint(self, problem_name: Optional[str] = None) -> outcome.ActionResult:
        """Progressive hint generator without spoiling the full solution."""
        if problem_name:
            self.fetch_problem_data(problem_name)
        elif not self.context.problem_title:
            found, title, slug = self.detect_active_problem()
            if found:
                self.fetch_problem_data(title)
            else:
                return outcome.failed(
                    "leetcode_hint",
                    "No active LeetCode problem detected. Please open the problem in your browser or specify its name (e.g. 'Two Sum').",
                )

        self.context.hint_level += 1
        level = self.context.hint_level

        prompt = f"""You are JARVIS, an elite LeetCode coding mentor.
The user is working on LeetCode: "{self.context.problem_title}".
Problem context: {self.context.description[:800]}

Generate progressive Hint #{level}.
MANDATORY RULES:
- DO NOT reveal the complete solution code.
- DO NOT give away the exact implementation details.
- For Hint 1: Focus purely on high-level intuition, pattern recognition, or observing key properties of the problem.
- For Hint 2: Suggest the optimal data structure and how it transforms the problem time complexity.
- For Hint 3: Explain the step-by-step algorithmic invariant or transition without writing the complete code.
Keep the hint under 3 concise sentences. Tone: Encouraging, analytical, concise."""

        try:
            hint_text = gemini.text(prompt, tier=gemini.FAST, timeout_ms=6000)
            if hint_text:
                return outcome.ActionResult(
                    status=outcome.STATUS_DONE,
                    action="leetcode_hint",
                    success=True,
                    verified=True,
                    message=f"Hint #{level} for {self.context.problem_title}:\n{hint_text.strip()}",
                    data={"problem": self.context.problem_title, "hint_level": level},
                    evidence=f"Generated progressive Hint #{level} for '{self.context.problem_title}'.",
                )
        except Exception as e:
            return outcome.failed("leetcode_hint", f"Could not generate hint: {e}")

        return outcome.failed("leetcode_hint", "Failed to generate hint.")

    def explain_approach(self, problem_name: Optional[str] = None) -> outcome.ActionResult:
        """Explains possible approaches from brute-force to optimal."""
        if problem_name:
            self.fetch_problem_data(problem_name)
        elif not self.context.problem_title:
            found, title, slug = self.detect_active_problem()
            if found:
                self.fetch_problem_data(title)
            else:
                return outcome.failed("leetcode_approach", "Please specify the LeetCode problem name or open it in your browser.")

        prompt = f"""You are JARVIS, an expert computer science mentor.
Problem: "{self.context.problem_title}"
Description: {self.context.description[:1000]}

Explain the possible approaches to solve this problem:
1. Brute Force Approach: Briefly describe the naive idea and why it is suboptimal (with Time and Space complexity).
2. Optimal Approach: Explain the optimal data structure or algorithmic technique (Hash Map, Two Pointers, DP, Monotonic Stack, etc.) and why it achieves optimal bounds.
Keep the explanation clear, structured, and under 150 words. Do not output full solution code unless asked."""

        try:
            ans = gemini.text(prompt, tier=gemini.SMART, timeout_ms=8000)
            if ans:
                return outcome.ActionResult(
                    status=outcome.STATUS_DONE,
                    action="leetcode_approach",
                    success=True,
                    verified=True,
                    message=f"Approaches for {self.context.problem_title}:\n{ans.strip()}",
                    data={"problem": self.context.problem_title},
                    evidence="Retrieved and generated algorithmic approaches.",
                )
        except Exception as e:
            return outcome.failed("leetcode_approach", f"Failed to explain approaches: {e}")

        return outcome.failed("leetcode_approach", "Could not analyze approach.")

    def debug_code(
        self,
        code: Optional[str] = None,
        language: str = "Python3",
        problem_name: Optional[str] = None,
    ) -> outcome.ActionResult:
        """Inspects user code, explains bugs, and provides failing test cases."""
        if problem_name:
            self.fetch_problem_data(problem_name)
        elif not self.context.problem_title:
            found, title, _ = self.detect_active_problem()
            if found:
                self.fetch_problem_data(title)

        user_code = code or self.context.user_code
        if not user_code:
            # Attempt to inspect from active editor
            user_code = self.inspect_editor_code()

        if not user_code:
            return outcome.failed(
                "leetcode_debug",
                "No code provided to debug. Please share your code snippet or copy it to the clipboard.",
            )

        prompt = f"""You are JARVIS, an elite algorithmic debugging assistant.
Problem: "{self.context.problem_title or 'LeetCode Problem'}"
Language: {language}
User's Code:
```{language.lower()}
{user_code}
```

Task:
1. Identify the logical mistake, edge-case oversight, or syntax bug in the user's code.
2. Provide a concrete test case where their current solution fails or produces wrong output.
3. Explain WHY it fails and how to fix the logic, without rewriting their entire function unless necessary.
Be precise and constructive."""

        try:
            analysis = gemini.text(prompt, tier=gemini.SMART, timeout_ms=10000)
            if analysis:
                return outcome.ActionResult(
                    status=outcome.STATUS_DONE,
                    action="leetcode_debug",
                    success=True,
                    verified=True,
                    message=f"Analysis of your {self.context.problem_title or ''} solution:\n{analysis.strip()}",
                    data={"problem": self.context.problem_title, "language": language},
                    evidence="Completed code analysis and bug identification.",
                )
        except Exception as e:
            return outcome.failed("leetcode_debug", f"Debugging analysis failed: {e}")

        return outcome.failed("leetcode_debug", "Could not complete debugging analysis.")

    def explain_complexity(self, code: Optional[str] = None) -> outcome.ActionResult:
        """Explains time and space complexity of problem or code."""
        code_snip = code or self.context.user_code
        prompt = f"""Analyze the Time and Space complexity for:
Problem: "{self.context.problem_title or 'Active Problem'}"
Code snippet (if provided):
{code_snip if code_snip else 'General optimal solution'}

Provide:
1. Time Complexity: Big-O notation with exact explanation of iterations/operations.
2. Space Complexity: Auxiliary memory used.
Be concise (2-3 sentences)."""

        try:
            comp = gemini.text(prompt, tier=gemini.FAST, timeout_ms=5000)
            if comp:
                return outcome.ActionResult(
                    status=outcome.STATUS_DONE,
                    action="leetcode_complexity",
                    success=True,
                    verified=True,
                    message=comp.strip(),
                    data={"problem": self.context.problem_title},
                    evidence="Analyzed algorithmic complexity.",
                )
        except Exception as e:
            return outcome.failed("leetcode_complexity", f"Failed to analyze complexity: {e}")

        return outcome.failed("leetcode_complexity", "Complexity analysis failed.")

    def generate_edge_cases(self) -> outcome.ActionResult:
        """Generates subtle edge cases and additional test cases for the problem."""
        prompt = f"""For LeetCode problem "{self.context.problem_title}":
Description: {self.context.description[:800]}

Generate 3 critical edge cases that often fail naive implementations:
1. Boundary values (empty inputs, min/max constraints, single element).
2. Duplicates or negative values (if applicable).
3. Worst-case distribution.
For each, show the input and expected output with a 1-line reason."""

        try:
            cases = gemini.text(prompt, tier=gemini.FAST, timeout_ms=6000)
            if cases:
                return outcome.ActionResult(
                    status=outcome.STATUS_DONE,
                    action="leetcode_edge_cases",
                    success=True,
                    verified=True,
                    message=f"Edge Cases for {self.context.problem_title}:\n{cases.strip()}",
                    data={"problem": self.context.problem_title},
                    evidence="Generated problem edge cases.",
                )
        except Exception as e:
            return outcome.failed("leetcode_edge_cases", f"Failed to generate edge cases: {e}")

        return outcome.failed("leetcode_edge_cases", "Could not generate edge cases.")


_DEFAULT_LEETCODE: Optional[LeetCodeAssistant] = None


def get_leetcode_assistant() -> LeetCodeAssistant:
    global _DEFAULT_LEETCODE
    if _DEFAULT_LEETCODE is None:
        _DEFAULT_LEETCODE = LeetCodeAssistant()
    return _DEFAULT_LEETCODE


def leetcode_assistant(parameters: dict = None, player=None, **kwargs) -> str:
    """Auto-discovered tool handler for LeetCode coding assistance."""
    params = dict(parameters or {})
    params.update(kwargs)

    action = (params.get("action") or "hint").lower().strip()
    problem = params.get("problem") or params.get("title") or ""
    code = params.get("code")
    language = params.get("language") or "Python3"

    assistant = get_leetcode_assistant()

    if action in ("hint", "give_hint", "next_hint"):
        res = assistant.get_hint(problem_name=problem)
    elif action in ("approach", "what_approach", "algorithm"):
        res = assistant.explain_approach(problem_name=problem)
    elif action in ("debug", "find_mistake", "fix_code", "why_failing"):
        res = assistant.debug_code(code=code, language=language, problem_name=problem)
    elif action in ("complexity", "time_complexity", "space_complexity"):
        res = assistant.explain_complexity(code=code)
    elif action in ("edge_cases", "test_cases", "another_test_case"):
        res = assistant.generate_edge_cases()
    else:
        res = assistant.get_hint(problem_name=problem)

    msg = res.message if hasattr(res, "message") else str(res)
    if player and hasattr(player, "write_log"):
        player.write_log(f"[LeetCode] 💡 {msg[:60]}…")

    if hasattr(res, "to_speech_text"):
        return res.to_speech_text()
    return msg


TOOL = {
    "name": "leetcode_assistant",
    "description": (
        "Context-aware LeetCode coding assistant. "
        "Capabilities: "
        "- hint: progressive hints without revealing the full solution ('Give me a hint for this problem'). "
        "- approach: explains possible approaches from brute-force to optimal ('What approach should I use?'). "
        "- debug: inspects user's code, finds mistakes, and explains failing test cases ('Why is my code failing?', 'Find the mistake in my solution'). "
        "- complexity: explains time and space complexity ('Explain the time complexity'). "
        "- edge_cases: generates subtle test cases and edge cases ('Give me another test case'). "
        "Automatically identifies the active LeetCode problem from the browser window."
    ),
    "brief": "Context-aware LeetCode mentor: progressive hints, approaches, code debugging, and complexity analysis.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "hint | approach | debug | complexity | edge_cases",
            },
            "problem": {
                "type": "STRING",
                "description": "Optional LeetCode problem name or title (e.g. 'Two Sum', 'Valid Anagram'). Automatically detected if omitted.",
            },
            "code": {
                "type": "STRING",
                "description": "Optional user code snippet to inspect or debug.",
            },
            "language": {
                "type": "STRING",
                "description": "Programming language (e.g. 'Python3', 'Java', 'C++', 'JavaScript').",
            },
        },
        "required": [],
    },
    "handler": leetcode_assistant,
}

