# input/

Put your own material here (this folder is git-ignored — nothing in it is ever committed):

```
input/
├── script.txt              narration script (see README.md §6; script.hindi.txt / script.hinglish.txt for language versions)
├── logo.png                transparent PNG logo
├── assets/                 images, video clips, cut-out PNGs, fonts      (--assets-dir input/assets)
├── narration.wav           your recorded voice-over                      (--voiceover input/narration.wav)
├── music.mp3               background music you are licensed to use      (--background-music input/music.mp3)
└── reference/              reference videos for style analysis           (--reference-video input/reference/x.mp4)
```

Then: `./generate_video.sh --script input/script.txt --logo input/logo.png --assets-dir input/assets`
