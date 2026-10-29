# Application-integrated Slint backend patch

Published i-slint-backend-winit 1.18.1, crate checksum dae4fcf1b19e9cb109984d2af87d2b9485388c619265bff71dd523133888bee2, source revision372cf0ee5577c3dfec309a45e7b778ba4e81b734. Reproduced native invalidation regression on the original archive.

Only upstream source change: winitwindowadapter.rs, backport of https://github.com/slint-ui/slint/pull/13568 , head5203fd9b97972ee90d76278dcce784f550310188 / merged08a297b7151e52b98b500b4424a8dee770fc7b58. Before drawing on Windows, forward GetUpdateRect to the existing logical dirty-region mechanism. Ordinary app redraws retain partial rendering; no polling or permanent full redraw. Upstream licenses/copyrights remain intact. This source is integrated into Z30 Studio, is not a standalone toolkit distribution, and ships with the application source/notice; the application's top-level AboutSlint attribution remains.

No registry cache edits or nightly dependencies. Remove this local override only after adopting and validating a published release containing the fix. SOURCE.json records original and installed hashes; tools/native_gui_patch.py verifies them before build and package.
