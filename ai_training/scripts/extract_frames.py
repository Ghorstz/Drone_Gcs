from pathlib import Path

import cv2


# Folder containing your short videos
VIDEO_DIR = Path("ai_training/raw/videos")

# Folder where extracted images will be saved
OUTPUT_DIR = Path("ai_training/extracted_frames")

# Save approximately one image every second
SECONDS_BETWEEN_FRAMES = 1.0


def extract_frames(video_path: Path) -> None:
    """Extract frames from one video at a fixed time interval."""

    output_folder = OUTPUT_DIR / video_path.stem

    output_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    cap = cv2.VideoCapture(
        str(video_path)
    )

    if not cap.isOpened():
        print(
            f"ERROR: Could not open "
            f"{video_path.name}"
        )
        return

    fps = cap.get(
        cv2.CAP_PROP_FPS
    )

    if fps <= 0:
        fps = 30

    frame_interval = max(
        1,
        int(
            fps
            * SECONDS_BETWEEN_FRAMES
        )
    )

    frame_number = 0
    saved_number = 0

    while True:
        success, frame = cap.read()

        if not success:
            break

        if frame_number % frame_interval == 0:

            filename = (
                f"{video_path.stem}_"
                f"{saved_number:05d}.jpg"
            )

            output_path = (
                output_folder
                / filename
            )

            cv2.imwrite(
                str(output_path),
                frame
            )

            saved_number += 1

        frame_number += 1

    cap.release()

    print(
        f"{video_path.name}: "
        f"{saved_number} frames extracted"
    )


def main() -> None:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    video_extensions = [
        "*.mp4",
        "*.mov",
        "*.avi",
        "*.mkv",
    ]

    videos = []

    for extension in video_extensions:
        videos.extend(
            VIDEO_DIR.glob(extension)
        )

    if not videos:
        print(
            "No videos found in:"
        )

        print(VIDEO_DIR)

        return

    print(
        f"Found {len(videos)} "
        f"video(s)."
    )

    for video in videos:

        print(
            f"\nProcessing: "
            f"{video.name}"
        )

        extract_frames(video)

    print(
        "\nFrame extraction complete."
    )


if __name__ == "__main__":
    main()