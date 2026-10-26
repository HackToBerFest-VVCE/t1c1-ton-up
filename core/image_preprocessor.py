"""OpenCV image preprocessing pipeline for Kirana ledger photographs.

Provides grayscale conversion, CLAHE contrast enhancement, deskewing,
and safe margin cropping while preserving the original image.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image

try:
    import cv2

    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False


@dataclass
class PreprocessingResult:
    """Holds original and processed images along with transformation metadata."""

    original_image: np.ndarray
    processed_image: np.ndarray
    skew_angle: float = 0.0
    deskewed: bool = False
    contrast_enhanced: bool = False
    cropped: bool = False
    crop_box: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)
    original_dimensions: Tuple[int, int] = (0, 0)  # (width, height)
    processed_dimensions: Tuple[int, int] = (0, 0)

    def to_pil_original(self) -> Image.Image:
        """Converts original BGR image to RGB PIL Image."""
        if len(self.original_image.shape) == 2:
            return Image.fromarray(self.original_image)
        # OpenCV uses BGR by default; convert to RGB for PIL/Streamlit
        rgb = cv2.cvtColor(self.original_image, cv2.COLOR_BGR2RGB) if HAS_OPENCV else self.original_image
        return Image.fromarray(rgb)

    def to_pil_processed(self) -> Image.Image:
        """Converts processed image to PIL Image."""
        if len(self.processed_image.shape) == 2:
            return Image.fromarray(self.processed_image)
        rgb = cv2.cvtColor(self.processed_image, cv2.COLOR_BGR2RGB) if HAS_OPENCV else self.processed_image
        return Image.fromarray(rgb)

    def to_processed_bytes(self, ext: str = ".png") -> bytes:
        """Encodes processed image to PNG/JPEG bytes."""
        if HAS_OPENCV:
            success, buffer = cv2.imencode(ext, self.processed_image)
            if success:
                return buffer.tobytes()
        # Fallback using PIL
        pil_img = self.to_pil_processed()
        buf = io.BytesIO()
        fmt = "JPEG" if ext.lower() in (".jpg", ".jpeg") else "PNG"
        pil_img.save(buf, format=fmt)
        return buf.getvalue()


class ImagePreprocessor:
    """Performs adaptive preprocessing on handwritten kirana ledger photos."""

    def __init__(
        self,
        clahe_clip_limit: float = 2.0,
        clahe_tile_grid_size: Tuple[int, int] = (8, 8),
        max_skew_angle: float = 30.0,
        min_skew_angle: float = 0.5,
        margin_padding_px: int = 15,
        min_crop_area_ratio: float = 0.50,
        max_crop_area_ratio: float = 0.98,
    ) -> None:
        self.clahe_clip_limit = clahe_clip_limit
        self.clahe_tile_grid_size = clahe_tile_grid_size
        self.max_skew_angle = max_skew_angle
        self.min_skew_angle = min_skew_angle
        self.margin_padding_px = margin_padding_px
        self.min_crop_area_ratio = min_crop_area_ratio
        self.max_crop_area_ratio = max_crop_area_ratio

    def load_image(self, image_input: Union[bytes, np.ndarray, Image.Image]) -> np.ndarray:
        """Loads various input types into a standardized BGR numpy ndarray."""
        if isinstance(image_input, bytes):
            nparr = np.frombuffer(image_input, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR) if HAS_OPENCV else np.array(Image.open(io.BytesIO(image_input)))
            if img is None:
                raise ValueError("Could not decode image from provided bytes.")
            return img

        if isinstance(image_input, Image.Image):
            rgb = np.array(image_input.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR) if HAS_OPENCV else rgb

        if isinstance(image_input, np.ndarray):
            return image_input.copy()

        raise TypeError(f"Unsupported image input type: {type(image_input)}")

    def convert_to_grayscale(self, image: np.ndarray) -> np.ndarray:
        """Converts image to single-channel 8-bit grayscale."""
        if not HAS_OPENCV:
            if len(image.shape) == 3:
                return np.dot(image[..., :3], [0.2989, 0.5870, 0.1140]).astype(np.uint8)
            return image

        if len(image.shape) == 2:
            return image
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def enhance_contrast(self, gray: np.ndarray) -> np.ndarray:
        """Enhances contrast using Contrast Limited Adaptive Histogram Equalization (CLAHE)."""
        if not HAS_OPENCV:
            # Fallback min-max normalization
            min_val, max_val = np.min(gray), np.max(gray)
            if max_val > min_val:
                return ((gray - min_val) / (max_val - min_val) * 255).astype(np.uint8)
            return gray

        clahe = cv2.createCLAHE(
            clipLimit=self.clahe_clip_limit,
            tileGridSize=self.clahe_tile_grid_size,
        )
        return clahe.apply(gray)

    def detect_skew_angle(self, gray: np.ndarray) -> float:
        """Calculates text skew angle in degrees using minAreaRect on binary text pixels."""
        if not HAS_OPENCV:
            return 0.0

        # Otsu inverse threshold to isolate ink/text
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        pts = np.column_stack(np.where(thresh > 0))

        if len(pts) < 100:
            return 0.0

        # minAreaRect takes (x, y) coordinates
        pts_xy = np.fliplr(pts)
        rect = cv2.minAreaRect(pts_xy)
        angle = rect[-1]

        # OpenCV minAreaRect returns angle in [-90, 0)
        if angle < -45:
            angle = -(90 + angle)
        elif angle > 45:
            angle = 90 - angle
        else:
            angle = -angle

        return float(angle)

    def deskew(self, image: np.ndarray, angle: float) -> np.ndarray:
        """Rotates image around its center to correct skew angle."""
        if not HAS_OPENCV or abs(angle) < self.min_skew_angle or abs(angle) > self.max_skew_angle:
            return image

        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        # Use white border padding for documents
        rotated = cv2.warpAffine(
            image,
            m,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        return rotated

    def crop_margins(
        self, gray: np.ndarray
    ) -> Tuple[np.ndarray, Optional[Tuple[int, int, int, int]], bool]:
        """Safely detects page boundaries and removes excess non-content margins."""
        if not HAS_OPENCV:
            return gray, None, False

        h, w = gray.shape[:2]
        total_area = w * h

        # Otsu thresholding to find main content bounding contour
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return gray, None, False

        # Find bounding box enclosing all major text/contour content
        x_min, y_min = w, h
        x_max, y_max = 0, 0
        found_content = False

        for c in contours:
            area = cv2.contourArea(c)
            if area > 100:  # Filter minor noise dots
                x, y, cw, ch = cv2.boundingRect(c)
                x_min = min(x_min, x)
                y_min = min(y_min, y)
                x_max = max(x_max, x + cw)
                y_max = max(y_max, y + ch)
                found_content = True

        if not found_content:
            return gray, None, False

        # Apply safety padding
        pad = self.margin_padding_px
        x_crop = max(0, x_min - pad)
        y_crop = max(0, y_min - pad)
        w_crop = min(w - x_crop, (x_max - x_min) + 2 * pad)
        h_crop = min(h - y_crop, (y_max - y_min) + 2 * pad)

        crop_area = w_crop * h_crop
        area_ratio = crop_area / total_area

        # Only crop if content is within safe boundary thresholds
        if self.min_crop_area_ratio <= area_ratio <= self.max_crop_area_ratio:
            cropped = gray[y_crop : y_crop + h_crop, x_crop : x_crop + w_crop]
            return cropped, (x_crop, y_crop, w_crop, h_crop), True

        return gray, None, False

    def preprocess(
        self,
        image_input: Union[bytes, np.ndarray, Image.Image],
        enable_deskew: bool = True,
        enable_crop: bool = True,
    ) -> PreprocessingResult:
        """Executes full preprocessing pipeline: Grayscale -> CLAHE -> Deskew -> Crop."""
        original = self.load_image(image_input)
        orig_h, orig_w = original.shape[:2]

        # 1. Grayscale
        gray = self.convert_to_grayscale(original)

        # 2. CLAHE Contrast Enhancement
        enhanced = self.enhance_contrast(gray)

        # 3. Deskewing
        skew_angle = 0.0
        deskewed = False
        current = enhanced

        if enable_deskew:
            angle = self.detect_skew_angle(enhanced)
            if self.min_skew_angle <= abs(angle) <= self.max_skew_angle:
                current = self.deskew(enhanced, angle)
                skew_angle = angle
                deskewed = True

        # 4. Safe Margin Cropping
        cropped = False
        crop_box = None
        if enable_crop:
            current, box, was_cropped = self.crop_margins(current)
            if was_cropped:
                cropped = True
                crop_box = box

        proc_h, proc_w = current.shape[:2]

        return PreprocessingResult(
            original_image=original,
            processed_image=current,
            skew_angle=skew_angle,
            deskewed=deskewed,
            contrast_enhanced=True,
            cropped=cropped,
            crop_box=crop_box,
            original_dimensions=(orig_w, orig_h),
            processed_dimensions=(proc_w, proc_h),
        )


_GLOBAL_PREPROCESSOR: Optional[ImagePreprocessor] = None


def get_preprocessor() -> ImagePreprocessor:
    """Returns singleton ImagePreprocessor instance."""
    global _GLOBAL_PREPROCESSOR
    if _GLOBAL_PREPROCESSOR is None:
        _GLOBAL_PREPROCESSOR = ImagePreprocessor()
    return _GLOBAL_PREPROCESSOR
