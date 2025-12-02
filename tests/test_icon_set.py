from io import StringIO

from nova_navigator.icon_set import IconSet


def test_icon_glyphs():
    CSV_DATA = """
    # name,nerdfont,unicode

    "gear","U+f013",⚙️
    """

    icons = IconSet()
    icons.load_icons(StringIO(CSV_DATA.strip()))

    with open("/home/ein2hi/workspace/epicodic/nova-navigator/config/default/icons.csv", encoding="utf-8") as f:
        icons.load_icons(f)

    for name, icon in icons:
        print(f"{name}: {icon[0]}  {icon[1]}")
