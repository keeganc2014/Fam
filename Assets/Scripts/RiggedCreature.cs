using System;
using System.Collections.Generic;
using UnityEngine;

// A monster made from a rigged FBX (Tools/pack_fbx_rig.py -> Resources/Creatures/<name>.bytes, 'GTR2').
// It uses the FBX's OWN skeleton, skin weights, mesh parts (shotgun, machete, skull ...) and the FBX's OWN keyframed animation.
// Nothing is rebuilt: the bones are created exactly as they are in the file and the clip is played on them.
//
// The clip is an in-place cycle, so the cycle speed follows how fast the monster really moves (Monster calls AddDistance), and the body
// is lowered each frame so that its lowest finger touches the floor.
public class RiggedCreature : MonoBehaviour
{
    public bool chasing;        // set by Monster
    public Light glow;          // light on its face (set by Monster)

    // ---- tuning (metres of ground per animation cycle, as a share of the model's height) ----
    const float StrideShare = 1.8f;
    const float BlendIn = 8f;

    [Serializable] public class BoneJ { public string name; public int parent; public float[] pos, rot; }
    [Serializable] public class SubJ { public int count; public string tex, name; public bool alpha; public float[] color; }
    [Serializable] public class ClipJ { public string name; public float[] times, q; }
    [Serializable] public class HeaderJ { public BoneJ[] bones; public int n; public SubJ[] subs; public float height; public string[] parts; public ClipJ[] clips; }

    Transform root;             // scaled to the wanted height; origin on the floor
    Transform[] bones;
    Quaternion[] restRot;
    Vector3 restRootPos;
    Transform[] tips;           // finger tips / hands: the lowest one is kept on the floor
    ClipJ run;
    float phase, speedSmooth, lastDist, idleTime, lift, scale, strideMeters;
    int boneCount;

    public static RiggedCreature Create(Transform parent, string resource, float wantedHeight)
    {
        try
        {
            TextAsset ta = Resources.Load<TextAsset>(resource);
            if (ta == null) return null;
            byte[] d = ta.bytes;
            if (d.Length < 8 || d[0] != (byte)'G' || d[1] != (byte)'T' || d[2] != (byte)'R' || d[3] != (byte)'2') return null;
            int jl = BitConverter.ToInt32(d, 4);
            HeaderJ h = JsonUtility.FromJson<HeaderJ>(System.Text.Encoding.UTF8.GetString(d, 8, jl));
            if (h == null || h.bones == null || h.n <= 0 || h.height <= 0.01f) return null;
            int o = 8 + jl, n = h.n;

            var pos = new float[n * 3]; Buffer.BlockCopy(d, o, pos, 0, n * 12); o += n * 12;
            var nrm = new float[n * 3]; Buffer.BlockCopy(d, o, nrm, 0, n * 12); o += n * 12;
            var uv = new float[n * 2]; Buffer.BlockCopy(d, o, uv, 0, n * 8); o += n * 8;
            var bi = new byte[n * 4]; Buffer.BlockCopy(d, o, bi, 0, n * 4); o += n * 4;
            var bw = new float[n * 4]; Buffer.BlockCopy(d, o, bw, 0, n * 16); o += n * 16;

            var go = new GameObject("Creature");
            go.layer = GtagBootstrap.PlayerLayer;
            go.transform.SetParent(parent, false);
            float scale = wantedHeight / h.height;
            go.transform.localScale = Vector3.one * scale;
            var c = go.AddComponent<RiggedCreature>();
            c.root = go.transform; c.scale = scale;
            c.strideMeters = StrideShare * wantedHeight;

            // bones exactly as in the FBX
            c.bones = new Transform[h.bones.Length];
            c.restRot = new Quaternion[h.bones.Length];
            for (int i = 0; i < h.bones.Length; i++)
            {
                BoneJ b = h.bones[i];
                var t = new GameObject(b.name).transform;
                t.gameObject.layer = GtagBootstrap.PlayerLayer;
                t.SetParent(b.parent >= 0 ? c.bones[b.parent] : go.transform, false);
                t.localPosition = new Vector3(b.pos[0], b.pos[1], b.pos[2]);
                t.localRotation = new Quaternion(b.rot[0], b.rot[1], b.rot[2], b.rot[3]);
                c.bones[i] = t; c.restRot[i] = t.localRotation;
            }
            c.boneCount = c.bones.Length;

            var meshGo = new GameObject("Skin");
            meshGo.layer = GtagBootstrap.PlayerLayer;
            meshGo.transform.SetParent(go.transform, false);
            var binds = new Matrix4x4[c.bones.Length];
            for (int i = 0; i < binds.Length; i++) binds[i] = c.bones[i].worldToLocalMatrix * meshGo.transform.localToWorldMatrix;

            var verts = new Vector3[n]; var norms = new Vector3[n]; var uvs = new Vector2[n]; var weights = new BoneWeight[n];
            for (int i = 0; i < n; i++)
            {
                verts[i] = new Vector3(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
                norms[i] = new Vector3(nrm[i * 3], nrm[i * 3 + 1], nrm[i * 3 + 2]);
                uvs[i] = new Vector2(uv[i * 2], uv[i * 2 + 1]);
                var w = new BoneWeight();
                w.boneIndex0 = bi[i * 4]; w.weight0 = bw[i * 4];
                w.boneIndex1 = bi[i * 4 + 1]; w.weight1 = bw[i * 4 + 1];
                w.boneIndex2 = bi[i * 4 + 2]; w.weight2 = bw[i * 4 + 2];
                w.boneIndex3 = bi[i * 4 + 3]; w.weight3 = bw[i * 4 + 3];
                weights[i] = w;
            }
            var mesh = new Mesh { name = "Creature" };
            if (n > 65000) mesh.indexFormat = UnityEngine.Rendering.IndexFormat.UInt32;
            mesh.vertices = verts; mesh.normals = norms; mesh.uv = uvs; mesh.boneWeights = weights; mesh.bindposes = binds;
            mesh.subMeshCount = h.subs.Length;
            var mats = new Material[h.subs.Length];
            for (int s = 0; s < h.subs.Length; s++)
            {
                SubJ sj = h.subs[s];
                var idx = new int[sj.count]; Buffer.BlockCopy(d, o, idx, 0, sj.count * 4); o += sj.count * 4;
                mesh.SetTriangles(idx, s);
                Color col = sj.color != null && sj.color.Length >= 3 ? new Color(sj.color[0], sj.color[1], sj.color[2], 1f) : Color.white;
                Texture2D tex = string.IsNullOrEmpty(sj.tex) ? null : Resources.Load<Texture2D>(sj.tex);
                Material m = sj.alpha ? MonkeModel.NewUnlitMaterial(col) : MonkeModel.NewMaterial(col);
                if (tex != null) m.mainTexture = tex;
                mats[s] = m;
            }
            mesh.RecalculateBounds();
            var smr = meshGo.AddComponent<SkinnedMeshRenderer>();
            smr.sharedMesh = mesh; smr.bones = c.bones; smr.rootBone = c.bones.Length > 0 ? c.bones[0] : go.transform;
            smr.sharedMaterials = mats;
            smr.updateWhenOffscreen = true;          // the animation moves the bones far outside the rest-pose bounds

            // the clip to play (the FBX's own cycle)
            if (h.clips != null)
                foreach (ClipJ cl in h.clips)
                    if (cl.times != null && cl.times.Length > 1 && cl.q != null && cl.q.Length == c.bones.Length * cl.times.Length * 4) { c.run = cl; break; }

            // fingertips / hands decide where the floor is
            var tips = new List<Transform>();
            foreach (Transform b in c.bones)
                if (b.name.EndsWith("_03_L") || b.name.EndsWith("_03_R") || b.name.StartsWith("thumb_03")) tips.Add(b);
            if (tips.Count == 0) foreach (Transform b in c.bones) if (b.name.StartsWith("hand")) tips.Add(b);
            c.tips = tips.ToArray();
            c.restRootPos = go.transform.localPosition;
            return c;
        }
        catch (Exception e)
        {
            Debug.LogWarning("RiggedCreature " + resource + ": " + e.Message);
            return null;
        }
    }

    public void AddDistance(float meters)
    {
        lastDist += meters;
        phase = Mathf.Repeat(phase + meters / strideMeters, 1f);
    }

    void LateUpdate()
    {
        if (root == null) return;
        float dt = Time.deltaTime;
        float speed = dt > 0f ? lastDist / dt : 0f;
        lastDist = 0f;
        speedSmooth = Mathf.Lerp(speedSmooth, speed, 1f - Mathf.Exp(-BlendIn * dt));
        float moving = Mathf.Clamp01(speedSmooth / 0.6f);
        idleTime += dt;

        if (run != null)
        {
            // idle: hold the first frame (the clip's start) with a slow breath; moving: play the cycle with the distance walked
            float len = run.times[run.times.Length - 1];
            float t = moving > 0.02f ? phase * len : 0f;
            Pose(t, moving);
        }

        // keep the lowest fingertip on the floor (the clip itself has no root height)
        float low = 0f;
        if (tips != null && tips.Length > 0)
        {
            low = float.MaxValue;
            for (int i = 0; i < tips.Length; i++) low = Mathf.Min(low, root.InverseTransformPoint(tips[i].position).y);
        }
        float target = -low * scale;      // tip height is in model units, the root sits in its parent's metres
        lift = Mathf.Lerp(lift, target, 1f - Mathf.Exp(-20f * dt));
        root.localPosition = restRootPos + new Vector3(0f, lift, 0f);
    }

    // local rotations of every bone from the clip at time t; blended with the rest pose when standing
    void Pose(float t, float moving)
    {
        float[] times = run.times; int nk = times.Length;
        int j = 0;
        while (j < nk - 2 && times[j + 1] <= t) j++;
        float span = times[j + 1] - times[j];
        float u = span > 1e-6f ? Mathf.Clamp01((t - times[j]) / span) : 0f;
        for (int b = 0; b < boneCount; b++)
        {
            int o0 = (b * nk + j) * 4, o1 = (b * nk + j + 1) * 4;
            var qa = new Quaternion(run.q[o0], run.q[o0 + 1], run.q[o0 + 2], run.q[o0 + 3]);
            var qb = new Quaternion(run.q[o1], run.q[o1 + 1], run.q[o1 + 2], run.q[o1 + 3]);
            Quaternion q = Quaternion.Slerp(qa, qb, u);
            bones[b].localRotation = Quaternion.Slerp(restRot[b], q, moving);     // standing = the FBX's rest (bind) pose
        }
    }
}
