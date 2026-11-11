
import tkinter as tk

from ui import StormRadarApp


def main() -> None:
    root = tk.Tk()
    StormRadarApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
