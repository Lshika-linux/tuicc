"""FakeStdscr: the entire stdscr contract any code in this codebase
actually needs, shared across test_draw_smoke.py (which only needs
.addstr()) and test_frame_update.py's update_frame() tests (which also
call .timeout()/.erase()/.getmaxyx() directly — see frame_update.py's
own module docstring for why that function was assumed untestable
without a real curses screen, same premise draw() turned out not to
need either).
"""


class FakeStdscr:
    def __init__(self, term_height=55, term_width=200):
        self.calls = []
        self.term_height = term_height
        self.term_width = term_width
        self.timeout_calls = []
        self.erase_calls = 0
        self.refresh_calls = 0

    def addstr(self, y, x, text, attr=0):
        self.calls.append((y, x, text, attr))

    def timeout(self, ms):
        self.timeout_calls.append(ms)

    def erase(self):
        self.erase_calls += 1

    def getmaxyx(self):
        return (self.term_height, self.term_width)

    def refresh(self):
        self.refresh_calls += 1

    def redrawln(self, beg, num):
        pass
