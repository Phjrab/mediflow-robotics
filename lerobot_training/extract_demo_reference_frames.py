"""Extract timestamped demonstration frames for offline rollout comparison."""

import argparse
import json
from pathlib import Path

import av
import pyarrow.parquet as pq
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--episode", type=int, default=15)
    args = parser.parse_args()
    episodes = pq.read_table(next((args.dataset / "meta/episodes").rglob("*.parquet"))).to_pylist()
    episode = next(e for e in episodes if e["episode_index"] == args.episode)
    prefix = "videos/observation.images.ceiling_oblique"
    path = args.dataset / prefix / f"chunk-{episode[prefix+'/chunk_index']:03d}" / f"file-{episode[prefix+'/file_index']:03d}.mp4"
    start = episode[prefix + "/from_timestamp"]
    end = episode[prefix + "/to_timestamp"]
    wanted = [start + seconds for seconds in range(0, int(end-start), 2)]
    images, audit = [], []
    with av.open(str(path)) as container:
        container.seek(int(start * av.time_base), backward=True)
        for frame in container.decode(video=0):
            stamp = float(frame.pts * frame.time_base)
            if wanted and stamp >= wanted[0] - .001:
                requested = wanted.pop(0)
                images.append(frame.to_image())
                audit.append({"requested_relative_s": requested-start, "decoded_relative_s": stamp-start})
            if not wanted:
                break
    args.output.mkdir(parents=True, exist_ok=True)
    grid = Image.new("RGB", (1440, ((len(images)+2)//3)*390), "white")
    draw = ImageDraw.Draw(grid)
    for index, (image, item) in enumerate(zip(images, audit)):
        image.thumbnail((480, 360))
        x, y = (index % 3)*480, (index//3)*390
        grid.paste(image, (x, y+25))
        draw.text((x+8, y+5), f"Demo {args.episode} / {item['decoded_relative_s']:.2f} s", fill="black")
    grid.save(args.output / f"successful_demo_ep{args.episode}_oblique.jpg", quality=90)
    (args.output / f"demo_ep{args.episode}_frame_audit.json").write_text(json.dumps(audit,indent=2)+"\n")
    print(f"Extracted {len(images)} frames from episode {args.episode}")


if __name__ == "__main__":
    main()
