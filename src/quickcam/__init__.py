"""QuickCam: a desktop control window for a USB camera, with pan, tilt and zoom where the camera has them."""

# The working name lives here and in pyproject.toml. Everything else reads these.
APP_NAME = "QuickCam"
APP_ID = "quickcam"  # command name, settings folder, socket
# The window's app id and the name of the .desktop file and icon. It differs from APP_ID so that
# window-manager rules written for other things called "quickcam" do not catch this window.
DESKTOP_ID = "quickcam-app"
__version__ = "0.1.0"
