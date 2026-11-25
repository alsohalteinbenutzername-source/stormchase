
import customtkinter as ctk

from ui import StormRadarApp


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    StormRadarApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
