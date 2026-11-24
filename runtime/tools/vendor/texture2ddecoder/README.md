# Texture2DDecoder ETC support

These unmodified ETC decoder files come from
https://github.com/K0lb3/texture2ddecoder at commit
`3ebc3b758bd6b1a3108b50148f7998ee34f058c6` (MIT; see LICENSE).

The Mac pointer-pose loader uses ETC1 for RGB/A8 atlases and ETC2/EAC for the
pinned game's format-150 RGBA atlases. `pvz2-cursor-texture.mm` converts the
library's BGRA output to the atlas loader's RGBA layout.
