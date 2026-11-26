using System.Text;
using TMPro;
using UnityEngine;

// Server stats on the second green board - the same information the GorillaServerStats mod (com.thaterror404.gorillatag.GorillaServerStats)
// writes on the forest sign in the real game:
//     LOBBY CODE / PLAYERS / MASTER / ACTIVE PLAYERS / PLAY TIME / PING   (+ how many lobbies you have joined)
//     and, when you are in no room:  WELCOME TO GORILLA TAG! / PLEASE JOIN A ROOM FOR STATS TO APPEAR!
// The mod itself is a BepInEx plugin for the real game, so it cannot run inside this standalone project; this is the same board, same
// texts, driven by this game's own online lobby. It is drawn with the menu's Minecraft-like Daydream font, on both faces of the board.
public class ServerStatsBoard : MonoBehaviour
{
    const string LobbiesKey = "serverstats_total_lobbies";     // the mod keeps this in its config.json as "totalLobbies"
    const float FaceOffset = 0.032f;                           // the green panel sits a little inside its frame

    readonly System.Collections.Generic.List<TMP_Text> faces = new System.Collections.Generic.List<TMP_Text>();
    float next, joinedAt = -1f;
    bool wasInRoom;
    int lobbies;

    // label colour (chalk yellow) and value colour (chalk white)
    const string Lab = "<color=#E8E27A>";
    const string Val = "<color=#F4F4EA>";

    public static ServerStatsBoard Create(Vector3 center, Vector3 normal, Vector3 up, float w, float h)
    {
        TMP_FontAsset font = MainMenu.LoadDaydream();
        if (font == null) { Debug.LogWarning("[GtagTest] Server stats board: the Daydream font could not be loaded."); return null; }

        // our own SDF material, one-sided so the two faces never show through each other
        Material mat = MainMenu.MakeTextMaterial(font);
        if (mat != null) mat.SetFloat("_Cull", 2f);

        var go = new GameObject("ServerStatsBoard");
        var b = go.AddComponent<ServerStatsBoard>();
        b.lobbies = PlayerPrefs.GetInt(LobbiesKey, 0);

        Quaternion front = Quaternion.LookRotation(-normal, up);            // forward points away from the viewer, as on the leaderboard
        b.faces.Add(MakeFace("Front", go.transform, font, mat, center + normal * FaceOffset, front, w, h));
        b.faces.Add(MakeFace("Back", go.transform, font, mat, center - normal * FaceOffset, front * Quaternion.Euler(0f, 180f, 0f), w, h));
        return b;
    }

    static TMP_Text MakeFace(string name, Transform parent, TMP_FontAsset font, Material mat, Vector3 pos, Quaternion rot, float w, float h)
    {
        var g = new GameObject(name);
        g.transform.SetParent(parent, false);
        g.transform.SetPositionAndRotation(pos, rot);
        var t = g.AddComponent<TextMeshPro>();
        t.font = font;
        if (mat != null) t.fontSharedMaterial = mat;
        t.richText = true;
        t.alignment = TextAlignmentOptions.TopLeft;
        t.color = Color.white;
        t.rectTransform.sizeDelta = new Vector2(w * 0.88f, h * 0.88f);       // metres: inside the green
        t.enableAutoSizing = true;
        t.fontSizeMin = 0.2f;
        t.fontSizeMax = 1.3f;                                                 // chalk-board sized: it never grows bigger than this
        t.overflowMode = TextOverflowModes.Truncate;
        t.text = "";
        return t;
    }

    static string Clock(float seconds)
    {
        int s = Mathf.Max(0, (int)seconds);
        return (s / 3600) + "H " + (s / 60 % 60) + "M " + (s % 60) + "S";      // the font has no colon, so hours / minutes / seconds are spelled
    }

    void Update()
    {
        if (Time.unscaledTime < next || faces.Count == 0) return;
        next = Time.unscaledTime + 0.5f;

        OnlineManager om = OnlineManager.Instance;
        bool inRoom = om != null && om.InRoom;
        if (inRoom && !wasInRoom)
        {
            joinedAt = Time.unscaledTime;                       // "Joined a room."
            lobbies++;
            PlayerPrefs.SetInt(LobbiesKey, lobbies);
        }
        else if (!inRoom && wasInRoom) joinedAt = -1f;          // "Left a room."
        wasInRoom = inRoom;

        var sb = new StringBuilder();
        if (!inRoom)
        {
            sb.Append(Val).Append("WELCOME TO GORILLA TAG!</color>\n\n");
            sb.Append(Lab).Append("PLEASE JOIN A ROOM FOR STATS TO APPEAR!</color>");
        }
        else
        {
            int players = om.PlayerCount;
            string master = MainMenu.Clean(om.MasterName).ToUpperInvariant();
            if (om.IsHost) master += " (YOU)";
            sb.Append(Lab).Append("LOBBY CODE</color>  ").Append(Val).Append(MainMenu.Clean(om.LobbyCode)).Append("</color>\n");
            sb.Append(Lab).Append("PLAYERS</color>  ").Append(Val).Append(players).Append("</color>\n");
            sb.Append(Lab).Append("MASTER</color>  ").Append(Val).Append(master).Append("</color>\n");
            sb.Append(Lab).Append("ACTIVE PLAYERS</color>  ").Append(Val).Append(players).Append("</color>\n");
            sb.Append(Lab).Append("PLAY TIME</color>  ").Append(Val).Append(Clock(Time.unscaledTime - joinedAt)).Append("</color>\n");
            sb.Append(Lab).Append("PING</color>  ").Append(Val).Append(om.PingMs).Append(" MS</color>\n");
            sb.Append(Lab).Append("LOBBIES JOINED</color>  ").Append(Val).Append(lobbies).Append("</color>");
        }
        string txt = sb.ToString();
        foreach (TMP_Text t in faces) if (t != null && t.text != txt) t.text = txt;
    }
}
