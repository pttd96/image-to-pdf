import io
import math
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image


def _order_points(pts: np.ndarray) -> np.ndarray:
    """
    Orders 4 points in clockwise order:
    [top-left, top-right, bottom-right, bottom-left]
    """
    rect = np.zeros((4, 2), dtype="float32")

    # Sum of coordinates: top-left has smallest sum, bottom-right has largest sum
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    # Difference of coordinates: top-right has smallest diff (x - y), bottom-left has largest diff
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]

    return rect


def find_document_corners(image: np.ndarray) -> Optional[np.ndarray]:
    """
    Detects the 4 corners of a document page in the image.
    Returns ordered (4, 2) float32 coordinates or None if no quad detected.
    """
    h, w = image.shape[:2]
    total_area = w * h

    # Resize for faster edge detection if image is very large
    max_dim = 1200
    scale = 1.0
    if max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        resized = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        resized = image.copy()

    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Multi-pass edge detection
    # 1. Canny
    edges = cv2.Canny(blurred, 30, 150)
    # Morphological close to join disconnected edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]

    for c in contours:
        area = cv2.contourArea(c)
        # Document should take at least 15% of the frame
        if area < (0.15 * (resized.shape[0] * resized.shape[1])):
            continue

        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)

        if len(approx) == 4 and cv2.isContourConvex(approx):
            pts = approx.reshape(4, 2).astype("float32")
            # Scale back to original resolution
            pts = pts / scale
            return _order_points(pts)

    # Fallback: Otsu thresholding if Canny didn't find clear quad
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    closed_otsu = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=3)
    contours_otsu, _ = cv2.findContours(closed_otsu, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours_otsu = sorted(contours_otsu, key=cv2.contourArea, reverse=True)[:5]

    for c in contours_otsu:
        area = cv2.contourArea(c)
        if area < (0.20 * (resized.shape[0] * resized.shape[1])):
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            pts = approx.reshape(4, 2).astype("float32")
            pts = pts / scale
            return _order_points(pts)

    return None


def warp_perspective_a4(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """
    Warps a 4-corner document region into a frontal rectangular plane
    matching the standard A4 aspect ratio (1 : 1.4142).
    """
    (tl, tr, br, bl) = corners

    # Compute width of new image
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_w = int(max(width_a, width_b))

    # Compute height of new image
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_h = int(max(height_a, height_b))

    # Standard A4 ratio constraint: height should be roughly sqrt(2) * width
    # If the document is vertical, adjust height to maintain crisp A4 proportions
    if max_h >= max_w:
        target_w = max_w
        target_h = int(max_w * 1.4142)
    else:
        # Landscape
        target_w = max_w
        target_h = int(max_w / 1.4142)

    target_w = max(400, target_w)
    target_h = max(560, target_h)

    dst = np.array(
        [
            [0, 0],
            [target_w - 1, 0],
            [target_w - 1, target_h - 1],
            [0, target_h - 1],
        ],
        dtype="float32",
    )

    M = cv2.getPerspectiveTransform(corners, dst)
    warped = cv2.warpPerspective(
        image,
        M,
        (target_w, target_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return warped


def deskew_min_area(image: np.ndarray) -> np.ndarray:
    """
    Fallback deskewing using minAreaRect angle when 4 corners cannot be cleanly found.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10
    )

    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    # Normalize angle
    if angle < -45:
        angle = -(90 + angle)
    elif angle > 45:
        angle = 90 - angle

    # Only correct if subtle tilt (< 15 degrees)
    if abs(angle) > 0.5 and abs(angle) < 15.0:
        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            image,
            M,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return rotated

    return image


def rectify_document_bytes(image_bytes: bytes) -> bytes:
    """
    Main entry point: takes raw image bytes, performs 4-point perspective
    homography or deskewing, and returns the rectified image bytes as JPEG.
    Never raises an uncaught exception; safely returns original bytes if all else fails.
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return image_bytes

        corners = find_document_corners(img)
        if corners is not None:
            rectified = warp_perspective_a4(img, corners)
        else:
            rectified = deskew_min_area(img)

        # Light contrast enhancement (CLAHE on L-channel in LAB)
        lab = cv2.cvtColor(rectified, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        cl = clahe.apply(l)
        limg = cv2.merge((cl, a, b))
        enhanced = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)

        # Encode back to high-quality JPEG
        success, encoded = cv2.imencode(".jpg", enhanced, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if success:
            return encoded.tobytes()
        return image_bytes
    except Exception as e:
        print(f"[DEWARP] Error during rectification, returning original image: {e}")
        return image_bytes
