# Google Barkour vB asset migration

- Migrated from historical commit `dcc2743c`, path `autoadapter/libraries/robots/google_barkour_vb/1.0.0/assets/`.
- Canonical Direct-MuJoCo entrypoint: `scene.xml`, which includes `barkour_vb.xml`.
- `barkour_vb.xml` is the free-base 12-actuated Barkour vB model. Its 12 MuJoCo `general` actuators implement affine joint-position servos (`gainprm="50 0 0"`, `biasprm="0 -50 -0.5"`); the canonical foot contact `solimp` is retained.
- The 11 STL meshes are the complete mesh closure of `barkour_vb.xml`. MJX scenes, the hfield, URDF, image, and `fixed_*` diagnostic variants were not migrated.
- The XML, meshes, README, CHANGELOG, and license are unchanged from the source commit. The source package provenance is recorded in this note.
