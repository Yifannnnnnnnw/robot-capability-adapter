# Unitree G1 asset migration

- Migrated from historical commit `dcc2743c`, path `autoadapter/libraries/robots/unitree_g1/1.0.0/assets/`.
- Canonical Direct-MuJoCo entrypoint: `scene.xml`, which includes `g1.xml`.
- `g1.xml` is the 29-actuated G1 model: all 29 actuators are MuJoCo position actuators and the model has rubber-hand meshes but no articulated finger joints.
- The 35 STL meshes are the complete mesh closure of `g1.xml`. The MJLab policy XMLs and their policy scene were not migrated.
- The XML, meshes, and license are unchanged from the source commit. The source package has no README or CHANGELOG; provenance is retained here.
