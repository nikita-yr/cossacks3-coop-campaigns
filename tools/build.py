"""Build the CoopCampaigns mod files from the vanilla game data.

Run from anywhere:  python build.py [--base <other mod folder>]
Re-run after a game update or after installing mods/DLC with new campaigns.

--base: build on top of another mod that replaces the same scripts (e.g. Renaissance,
        workshop id 3398700006). Its versions of the scripts are patched instead of
        vanilla, and with Renaissance the online save/resume also keeps coop control.
        CoopCampaigns must then be loaded after that mod.

Produces:
  data/game/var/battles.cfg                       vanilla battles + one coop entry per campaign mission
  data/scripts/common.inc/initmaphistoricalbattle.inc   vanilla script + coop branch
"""
import os
import re
import sys

BASE = None
MOD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_DIR = os.path.dirname(os.path.dirname(MOD_DIR))
VANILLA = os.path.join(GAME_DIR, "data")
CRLF = "\r\n"
PREFIX = "      [*] = ;"

# Campaigns that make no sense in coop (tutorials).
SKIP_CAMPAIGNS = {"tutcam"}
MAX_PER_TEAM = 4


def read(path):
    with open(path, "r", encoding="cp1251", newline="") as f:
        return f.read().replace("\r\n", "\n")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="cp1251", newline="") as f:
        f.write(text.replace("\n", CRLF))
    print("written:", os.path.relpath(path, GAME_DIR))


def parse_missions(campaigns_cfg):
    """Yield (campaign dict, mission dict) using the fixed indentation of campaigns.cfg."""
    camp, mis = None, None
    for line in campaigns_cfg.split("\n"):
        m = re.match(r"^( +)(\w+) = (.*)$", line)
        if not m:
            continue
        indent, key, value = len(m.group(1)), m.group(2), m.group(3).strip()
        if indent == 6:  # campaign level
            if key == "Name":
                camp = {}
            camp[key] = value
        elif indent == 12:  # mission level
            if key == "Name":
                if mis:
                    yield mis_camp, mis
                mis, mis_camp = {}, camp
            mis[key] = value
    if mis:
        yield mis_camp, mis


def team_block(team, mission, slot_offset):
    out = [f"         {team} : struct.begin", "            PlayersCounts : struct.begin"]
    for count in range(1, MAX_PER_TEAM + 1):
        out += ["               [*] : struct.begin", f"                  Count = {count}",
                "                  PosList : struct.begin"]
        for _ in range(count):
            # Every human controls mission player index 0, so all labels point to it.
            out += ["                     [*] : struct.begin",
                    f"                        LocaleName = {mission}.player0",
                    "                        X = 0", "                        Y = 0",
                    "                     struct.end"]
        out += ["                  struct.end", "               struct.end"]
    out += ["            struct.end", "         struct.end"]
    return out


def coop_entry(camp, mis):
    name = mis["Name"]
    out = ["   [*] : struct.begin",
           f"      Name = coop_{name}",
           "      Coop = True",
           f"      Campaign = {camp.get('Name', '')}",
           f"      CampaignLocaleName = {camp.get('LocaleName', '')}",
           f"      LocaleName = {mis.get('LocaleName', name + '.name')}",
           f"      LocaleDescr = {mis.get('LocaleDescr', name + '.description')}",
           f"      LocalePath = {camp.get('LocalePath', '')}",
           "      Preview = ",
           f"      MapPath = {mis['MapPath']}",
           f"      MaxPlayers = {MAX_PER_TEAM * 2}",
           "      LocalePositions = missionplayers",
           "      LocalePositionsPath = .\\data\\locale\\%lang%\\missions\\missionplayers.lng",
           "      Positions : struct.begin"]
    out += team_block("Team1", name, 0) + team_block("Team2", name, 4)
    out += ["      struct.end", "   struct.end"]
    return out


def camp_locale(campaigns):
    m = re.search(r"LocalePath = (.*)", campaigns)
    return m.group(1).strip() if m else ""


def build_battles():
    battles = read(os.path.join(VANILLA, "game", "var", "battles.cfg")).rstrip()
    if not battles.endswith("section.end"):
        sys.exit("battles.cfg: unexpected format")
    body = battles[: -len("section.end")].rstrip("\n")
    campaigns = read(os.path.join(VANILLA, "game", "var", "campaigns.cfg"))
    entries, count = [], 0
    # pseudo-campaign of the coop room: its "missions" are the coop saves of the profile
    entries += coop_entry({"Name": "coopsaves", "LocaleName": "", "LocalePath": camp_locale(campaigns)},
                          {"Name": "saves", "LocaleName": "", "LocaleDescr": "", "MapPath": ""})
    missions = list(parse_missions(campaigns))
    missions.sort(key=lambda cm: bool(cm[0].get("RequireDLC", "").strip()))  # stable: keeps cfg order
    for camp, mis in missions:
        if camp.get("Name") in SKIP_CAMPAIGNS or "MapPath" not in mis:
            continue
        entries += coop_entry(camp, mis)
        count += 1
    write(os.path.join(MOD_DIR, "data", "game", "var", "battles.cfg"),
          body + "\n" + "\n".join(entries) + "\nsection.end\n")
    print(f"coop missions added: {count}")


# (anchor in vanilla code, replacement). Each anchor must occur exactly once.
PATCHES = [
    ("_misc_ResetProgressPlayer;\n\nCreatePlayers;",
     """// CoopCampaigns: a battles.cfg entry with Coop = True is a campaign mission played
// by several humans who all control mission player 0 (the campaign's human player).
function CoopIsCoopBattle : Boolean;
begin
   Result := False;
   if (not gMap.bbattle) then
   exit;
   var pBattles : Integer = _parser_ParserBattles(False);
   if (pBattles=0) then
   exit;
   var pBattle : Integer = ParserSelectByHandleByIndex(pBattles, gMap.battleind);
   if (pBattle<>0) then
   Result := ParserGetBoolValueByKeyByHandle(pBattle, 'Coop');
end;

var bCoop : Boolean = CoopIsCoopBattle;
if (bCoop) then
begin
   Log('CoopCampaigns: coop mission '+gMap.battlemap);
   var pCoopEntry : Integer = ParserSelectByHandleByIndex(_parser_ParserBattles(False), gMap.battleind);
   var coopMis : String = StrReplace(ParserGetValueByKeyByHandle(pCoopEntry, 'Name'), 'coop_', '');
   gScenario.missname := coopMis;
   gScenario.campname := ParserGetValueByKeyByHandle(pCoopEntry, 'Campaign');
   var k : Integer;
   for k:=0 to gc_MaxPlayerCount-1 do
   begin
      // lobby slots are only nominal here: the mission's own players with their own colors,
      // names and no lobby teams (diplomacy comes from the mission)
      gMap.players[k].color := k;
      gMap.players[k].team := 0;
      if (k>0) then
      begin
         var coopName : String = GetLocaleTableListItemByID('missionplayers', coopMis+'.player'+IntToStr(k));
         if (coopName='') then
         begin
            var namekey : String = gScenario.players[k].name;
            if (namekey<>'') then
            coopName := GetLocaleTableListItemByID(gScenario.settings.langtable, namekey);
            if (coopName='') then
            coopName := namekey;
         end;
         // lanid stays: the network still tracks the human whose lobby slot this is
         gMap.players[k].name := coopName;
      end;
      gMap.players[k].bexists := (k=0) or (gMap.players[k].name<>'') or (gPlayer[k].counter.farmused>0);
   end;
end;

_misc_ResetProgressPlayer;

CreatePlayers;"""),
    # keep the mission's own diplomacy (OnLoadFull re-applies gPlayer[].enemyplmask from the map)
    ("for i:=0 to gc_MaxPlayerCount-1 do\nbegin\n   gPlayer[i].enemyplmask := 0;",
     "if (not bCoop) then\nfor i:=0 to gc_MaxPlayerCount-1 do\nbegin\n   gPlayer[i].enemyplmask := 0;"),
    ("if (not bPlayerSet) then",
     "if (bCoop) then\nbegin\n   _control_SetPlayer(0);\n   bPlayerSet := True;\nend;\n\nif (not bPlayerSet) then"),
    ("   gScenario.difficulty := gc_player_difficulty_impossible;",
     """   gScenario.difficulty := gc_player_difficulty_impossible;
   if (bCoop) then
   begin
      // must be the same on every PC, so a constant instead of the local campaign setting
      for i:=0 to gc_MaxPlayerCount-1 do
      gPlayer[i].difficulty := gc_player_difficulty_normal;
      gScenario.difficulty := gc_player_difficulty_normal;
   end;"""),
    ("   for i:=0 to gc_MaxPlayerCount-1 do\n   gPlayer[i].team := gMap.players[i].team;",
     "   if (not bCoop) then\n   for i:=0 to gc_MaxPlayerCount-1 do\n   gPlayer[i].team := gMap.players[i].team;"),
]


def base_file(*rel):
    """Script from the --base mod if it has one, else vanilla."""
    if BASE:
        path = os.path.join(BASE, "data", *rel)
        if os.path.exists(path):
            return path
    return os.path.join(VANILLA, *rel)


def patch_inc(rel, patches):
    src = read(base_file(*rel))
    head, code, tail = [], [], []
    for line in src.split("\n"):
        if line.startswith(PREFIX):
            code.append(line[len(PREFIX):])
        elif code:
            tail.append(line)
        else:
            head.append(line)
    text = "\n".join(code)
    for anchor, repl in patches:
        n = text.count(anchor)
        if n != 1:
            sys.exit(f"{rel[-1]}: anchor found {n} times, game/mod update? -> {anchor[:50]!r}")
        text = text.replace(anchor, repl)
    out = head + [PREFIX + l for l in text.split("\n")] + tail
    write(os.path.join(MOD_DIR, "data", *rel), "\n".join(out))


# Renaissance online resume gives each peer its saved slot; in coop everyone must get player 0 back.
RESUME_PATCH = [("_resume_AfterLoad;", """_resume_AfterLoad;
// CoopCampaigns: after an online resume of a coop mission every human controls player 0 again
if (gOnlineResumeStage=5) and (gOnlineResumeDidload) and (gMap.bbattle) then
begin
   var pCoopBattles : Integer = _parser_ParserBattles(False);
   var pCoopBattle : Integer;
   if (pCoopBattles<>0) then
   pCoopBattle := ParserSelectByHandleByIndex(pCoopBattles, gMap.battleind);
   if (pCoopBattle<>0) and (ParserGetBoolValueByKeyByHandle(pCoopBattle, 'Coop')) and (not gbool_gui_bspectator) then
   begin
      Log('CoopCampaigns: resume, control player 0');
      _control_SetPlayer(0);
   end;
end;""")]


# Main menu button text per game language. Each game language uses its own code page (see
# data\locale\lang.loc fonts), so non-ASCII letters are written as #NNN char codes in that code page.
BUTTON_TEXT = {
    "en": ("Co-op", "cp1252"),
    "fr": ("Coopération", "cp1252"),
    "de": ("Koop", "cp1252"),
    "es": ("Cooperativo", "cp1252"),
    "it": ("Cooperativa", "cp1252"),
    "po": ("Cooperativo", "cp1252"),
    "pl": ("Kooperacja", "cp1250"),
    "cs": ("Kooperace", "cp1250"),
    "ru": ("Кооператив", "cp1251"),
    "uk": ("Кооператив", "cp1251"),
    "tu": ("Ortak Oyun", "cp1254"),
}


def pas_string(text, codepage):
    """Pascal string expression with non-ASCII letters as #NNN codes of the given code page."""
    parts, run = [], ""
    for ch in text:
        if ord(ch) < 128 and ch != "'":
            run += ch
            continue
        if run:
            parts.append("'" + run + "'")
            run = ""
        parts.append("#" + str(ch.encode(codepage)[0]))
    if run:
        parts.append("'" + run + "'")
    return "+".join(parts)


TEXTS = {
    "saves": {
        "en": "Saved games", "fr": "Parties sauvegardées", "de": "Gespeicherte Spiele",
        "es": "Partidas guardadas", "it": "Partite salvate", "po": "Jogos salvos",
        "pl": "Zapisane gry", "cs": "Uložené hry", "ru": "Сохранённые игры",
        "uk": "Збережені ігри", "tu": "Kayıtlı oyunlar"},
    "loadsave": {
        "en": "Load save", "fr": "Charger", "de": "Spielstand laden", "es": "Cargar partida",
        "it": "Carica partita", "po": "Carregar jogo", "pl": "Wczytaj zapis", "cs": "Načíst hru",
        "ru": "Загрузить сохранение", "uk": "Завантажити збереження", "tu": "Kayıt yükle"},
    "tocampaigns": {
        "en": "Campaigns", "fr": "Campagnes", "de": "Kampagnen", "es": "Campañas",
        "it": "Campagne", "po": "Campanhas", "pl": "Kampanie", "cs": "Kampaně",
        "ru": "К кампаниям", "uk": "До кампаній", "tu": "Seferler"},
    "pause": {
        "en": "Pause the game first, then save.",
        "fr": "Mettez d'abord le jeu en pause, puis sauvegardez.",
        "de": "Pausiere zuerst das Spiel, dann speichere.",
        "es": "Pausa primero la partida y luego guarda.",
        "it": "Metti prima il gioco in pausa, poi salva.",
        "po": "Pause o jogo primeiro e depois salve.",
        "pl": "Najpierw zatrzymaj grę (pauza), potem zapisz.",
        "cs": "Nejprve hru pozastavte, pak uložte.",
        "ru": "Сначала поставьте игру на паузу, затем сохраните.",
        "uk": "Спочатку поставте гру на паузу, потім збережіть.",
        "tu": "Önce oyunu duraklatın, sonra kaydedin."},
    "savehint": {
        "en": "Continue a co-op game saved earlier. Every player needs this save: it is created on the PC of everyone who was in the game.",
        "fr": "Reprenez une partie coop sauvegardée. Chaque joueur doit avoir cette sauvegarde : elle est créée sur le PC de tous les joueurs de la partie.",
        "de": "Setze ein gespeichertes Koop-Spiel fort. Jeder Spieler braucht diesen Spielstand: Er wird auf dem PC aller Teilnehmer erstellt.",
        "es": "Continúa una partida cooperativa guardada. Cada jugador necesita esta partida: se crea en el PC de todos los que jugaron.",
        "it": "Continua una partita cooperativa salvata. Ogni giocatore deve avere questo salvataggio: viene creato sul PC di tutti i partecipanti.",
        "po": "Continue um jogo cooperativo salvo. Cada jogador precisa deste save: ele é criado no PC de todos que estavam no jogo.",
        "pl": "Kontynuuj zapisaną grę w kooperacji. Każdy gracz musi mieć ten zapis: tworzy się on na komputerze każdego uczestnika gry.",
        "cs": "Pokračujte v uložené kooperativní hře. Každý hráč musí mít tuto uloženou hru: vytváří se na PC všech, kdo hráli.",
        "ru": "Продолжить сохранённую кооп-игру. Сохранение должно быть у каждого игрока: оно создаётся на компьютере у всех, кто был в игре.",
        "uk": "Продовжити збережену кооп-гру. Збереження має бути в кожного гравця: воно створюється на комп'ютері в усіх, хто був у грі.",
        "tu": "Kaydedilmiş bir ortak oyuna devam edin. Her oyuncuda bu kayıt olmalı: oyundaki herkesin bilgisayarında oluşturulur."},
}


TEXTS["coop"] = {lang: text for lang, (text, cp) in BUTTON_TEXT.items()}


def localized(var, key):
    """Pascal: set var to TEXTS[key] for the current game language (English by default)."""
    lines = [f"var {var} : String = {pas_string(TEXTS[key]['en'], 'cp1252')};",
             f"var {var}Loc : String;",
             f"_profile_ConvertConfigValueToLocale(gProfile.lang, {var}Loc);",
             f"case {var}Loc of"]
    for lang, text in TEXTS[key].items():
        lines.append(f"   '{lang}' : {var} := {pas_string(text, BUTTON_TEXT[lang][1])};")
    lines.append("end;")
    return "\n".join(lines)


COOP_BUTTON_CASES = "".join(
    f"   '{lang}' : cTextCoop := {pas_string(text, cp)};\n" for lang, (text, cp) in BUTTON_TEXT.items())

# Main menu "Co-op" button: runs the normal Multiplayer flow (whatever mods made of it),
# then preselects a historical-battle room and the first coop mission.
COOP_TAG = "const cTagCoop = 1150;"

SHOW_MENU_PATCH = [
    ("const cTagCredits = 111;", "const cTagCredits = 111;\n" + COOP_TAG),
    ("elmHnd := ShowButton('brandommap'", """var cTextCoop : String = 'Co-op';
var coopLocale : String;
_profile_ConvertConfigValueToLocale(gProfile.lang, coopLocale);
case coopLocale of
""" + COOP_BUTTON_CASES + """end;
elmHnd := ShowButton('bcoop', elmHnd, elmLeftFrame, cbtnbigmat, cTextCoop, cmargin2, cTagCoop, IsEngine);
SetGUIElementUseUserColor(elmHnd, True);
SetGUIElementUserColor(elmHnd, 1, 0.7, 0.5, 1);
SetGUIElementColor(GetGUIElementIndexByNameParent('txtbtn', elmHnd), 1, 0.9, 0.7, 1);
// the extra button must not push Exit off the screen: take its height from the gap above Exit
var coopExitMargin : Integer = cmargin2+cMarginAdd*2-(GetGUIElementHeight(elmHnd)+cmargin2);
if (coopExitMargin<cmargin2) then
coopExitMargin := cmargin2;
elmHnd := ShowButton('brandommap'"""),
    ("cmargin2+cMarginAdd*2, cTagExit", "coopExitMargin, cTagExit"),
]


def event_menu_patch(renaissance):
    if renaissance:
        room_mode = "         gOnlineResumeCreateMode := 1; // room type: historical battle\n"
    else:
        # vanilla keeps this flag until the room is created
        room_mode = "         gInternetShell.bhistoricalbattle := True; // room type: historical battle\n"
    return [
        ("const cTagCredits = 111;", "const cTagCredits = 111;\n" + COOP_TAG),
        ("      cTagMultiplayer : begin", """      cTagCoop : begin
         _gui_SendTagToState('EventMainMenu', cTagMultiplayer);
""" + room_mode + """         // the historical battle room picks a coop mission when it opens
         var pCoopIntent : Integer = ParserSelectByKey('coopcampaigns');
         if (pCoopIntent=0) then
         pCoopIntent := ParserCreate('coopcampaigns');
         ParserSetIntValueByKeyByHandle(pCoopIntent, 'intent', 1);
         // the lobby creates the coop room by itself right after login
         ParserSetIntValueByKeyByHandle(pCoopIntent, 'autocreate', 1);
      end;
      cTagMultiplayer : begin
         if (ParserSelectByKey('coopcampaigns')<>0) then
         begin
            ParserSetIntValueByKeyByHandle(ParserSelectByKey('coopcampaigns'), 'intent', 0);
            ParserSetIntValueByKeyByHandle(ParserSelectByKey('coopcampaigns'), 'autocreate', 0);
            ParserSetIntValueByKeyByHandle(ParserSelectByKey('coopcampaigns'), 'room', 0);
         end;"""),
    ]


SHOW_BATTLE_PATCH = [
    ("if (IsLanPublicServerMode) and (not _net_IsOnline) then", """// CoopCampaigns: coop room state
var pCoopBattles : Integer = _parser_ParserBattles(False);
var pCoopIntent : Integer = ParserSelectByKey('coopcampaigns');
if (pCoopIntent<>0) and (ParserGetIntValueByKeyByHandle(pCoopIntent, 'intent')=1) then
begin
   // opened from the main menu Co-op button: this room stays a coop room until it is left
   ParserSetIntValueByKeyByHandle(pCoopIntent, 'intent', 0);
   ParserSetIntValueByKeyByHandle(pCoopIntent, 'room', 1);
end;
var pCoopCur : Integer = ParserSelectByHandleByIndex(pCoopBattles, gMap.battleind);
if (pCoopIntent<>0) and (not bClient) and (ParserGetIntValueByKeyByHandle(pCoopIntent, 'room')=1)
and ((pCoopCur=0) or (not ParserGetBoolValueByKeyByHandle(pCoopCur, 'Coop'))) then
begin
   // coop room showing a normal battle (fresh room or reset settings): switch to the first coop mission
   var ci : Integer;
   for ci:=0 to ParserGetCountByHandle(pCoopBattles)-1 do
   if ParserGetBoolValueByKeyByHandle(ParserSelectByHandleByIndex(pCoopBattles, ci), 'Coop') and IsFileExists(ParserGetValueByKeyByHandle(ParserSelectByHandleByIndex(pCoopBattles, ci), 'MapPath')) then
   begin
      gMap.battleind := ci;
      gbool_gui_senddatasyncparser := True;
      break;
   end;
end;
var pCoopSel : Integer = ParserSelectByHandleByIndex(pCoopBattles, gMap.battleind);
var bCoop : Boolean = (pCoopSel<>0) and ParserGetBoolValueByKeyByHandle(pCoopSel, 'Coop');
var coopCampaign : String;
if (bCoop) then
coopCampaign := ParserGetValueByKeyByHandle(pCoopSel, 'Campaign');

if (IsLanPublicServerMode) and (not _net_IsOnline) then"""),
    ("   if not bTeam1 or not bTeam2 then\n   bAllowNextStage := False;\nend;",
     """   if not bTeam1 or not bTeam2 then
   bAllowNextStage := False;
   if (bCoop) then
   begin
      // everyone controls the campaign nation: teams are assigned automatically on start
      bTeamUnknown := False;
      bTeam1 := True;
      bTeam2 := True;
      bAllowNextStage := True;
      bAllowReady := True;
   end;
end;"""),
    ("      var mapPath : String = ParserGetValueByKeyByHandle(pBattle, 'MapPath');",
     """      var mapPath : String = ParserGetValueByKeyByHandle(pBattle, 'MapPath');
      // coop room lists the missions of the selected campaign, a normal room only battles
      var bEntryCoop : Boolean = ParserGetBoolValueByKeyByHandle(pBattle, 'Coop');
      var bEntryShown : Boolean = (bEntryCoop=bCoop) and ((not bEntryCoop) or ((ParserGetValueByKeyByHandle(pBattle, 'Campaign')=coopCampaign) and IsFileExists(mapPath)));"""),
    ("         GUIListBoxAddItem(elmComboChooseMap, locale, i)",
     "         if (bEntryShown) then\n         GUIListBoxAddItem(elmComboChooseMap, locale, i)"),
    ("SetGUIListBoxItemIndexSilent(elmComboChooseMap, gMap.battleind);",
     "SetGUIListBoxItemIndexSilent(elmComboChooseMap, GetGUIListBoxItemIndexOfTag(elmComboChooseMap, gMap.battleind));"),
    ("var elmPreviewHnd : Integer = _gui_CreateSkinWindow('map_preview_container', elmMain, gc_halParentLeft, gc_valParentTop, 674-cAddOffsetX+cAddOffsetL, 57+GetGUIElementHeight(elmHnd), 283+cAddOffsetX, 283-29, 1, bupdate);",
     """var elmPreviewHnd : Integer = _gui_CreateSkinWindow('map_preview_container', elmMain, gc_halParentLeft, gc_valParentTop, 674-cAddOffsetX+cAddOffsetL, 57+GetGUIElementHeight(elmHnd), 283+cAddOffsetX, 283-29, 1, bupdate);
// CoopCampaigns: coop layout of the right column, top to bottom:
//   campaign chooser (in place of the battle chooser), mission chooser, description.
// Combobox item tags are battles.cfg indices, like the battle chooser.
""" + localized("cTextCoopSaves", "saves") + """
procedure CoopFitText(var text : String; maxw : Integer);
begin
   // cut long names with "..." so they do not run under the combobox arrow
   if (_gui_GetFontTextWidth(text, gc_font_serif_12)>maxw) then
   begin
      var cut : String = text+'...';
      while (_gui_GetFontTextWidth(cut, gc_font_serif_12)>maxw) and (StrLength(text)>4) do
      begin
         text := SubStr(text, 1, StrLength(text)-1);
         cut := text+'...';
      end;
      text := cut;
   end;
end;

var coopDescrOff : Integer;
var elmCoopOld : Integer;
if (bCoop) then
begin
   coopDescrOff := 30;
   var bCoopEdit : Boolean = (bServer or bOffline) and (gMap.battlestage=0);
   SetGUIElementVisible(elmComboChooseMap, False);
   var elmComboCamp : Integer = _gui_CreateComboBox('campchooser', elmHnd, count, 0, gc_halParentLeft, gc_valParentTop, 3, 3, GetGUIElementWidth(elmHnd)-6, gc_halLeft, gc_valMiddle, 5, 0, gc_font_serif_12, True, eventstate, bupdate, True);
   var elmComboMis : Integer = _gui_CreateComboBox('missionchooser', elmPreviewHnd, count, 0, gc_halParentLeft, gc_valParentTop, 3, 3, GetGUIElementWidth(elmPreviewHnd)-6, gc_halLeft, gc_valMiddle, 5, 0, gc_font_serif_12, True, eventstate, bupdate, True);
   GUIListBoxClearSilent(elmComboCamp);
   GUIListBoxClearSilent(elmComboMis);
   SetGUIElementEnabled(elmComboCamp, gMap.battlestage=0);
   SetGUIElementEnabled(elmComboMis, gMap.battlestage=0);
   SetGUIAllowEvents(elmComboCamp, bCoopEdit, False, False);
   SetGUIAllowEvents(elmComboMis, bCoopEdit, False, False);
   SetGUIElementVisible(GetGUIElementIndexByNameParent('btn', elmComboCamp), bCoopEdit);
   SetGUIElementVisible(GetGUIElementIndexByNameParent('btn', elmComboMis), bCoopEdit);
   var campMaxW : Integer = GetGUIElementWidth(elmComboCamp)-34;
   var misMaxW : Integer = GetGUIElementWidth(elmComboMis)-34;
   var coopSeen : String;
   var coopSelCamp, coopSelMis : Integer;
   var ci : Integer;
   for ci:=0 to count-1 do
   begin
      var pB : Integer = ParserSelectByHandleByIndex(pBattles, ci);
      var campName : String = ParserGetValueByKeyByHandle(pB, 'Campaign');
      if ParserGetBoolValueByKeyByHandle(pB, 'Coop') and ((campName='coopsaves') or IsFileExists(ParserGetValueByKeyByHandle(pB, 'MapPath'))) then
      begin
         if (not StrExists(coopSeen, '|'+campName+'|')) then
         begin
            coopSeen := coopSeen+'|'+campName+'|';
            var campText : String = GetLocaleTableListItemByID('missions', ParserGetValueByKeyByHandle(pB, 'CampaignLocaleName'));
            if (campName='coopsaves') then
            campText := cTextCoopSaves
            else
            if (campText='') then
            campText := campName;
            CoopFitText(campText, campMaxW);
            GUIListBoxAddItem(elmComboCamp, campText, ci);
            if (campName=coopCampaign) then
            coopSelCamp := GetGUIListBoxItemsCount(elmComboCamp)-1;
         end;
         if (campName=coopCampaign) and (campName<>'coopsaves') then
         begin
            var misText : String = GetLocaleTableListItemByID('missions', ParserGetValueByKeyByHandle(pB, 'LocaleName'));
            if (misText='') then
            misText := ParserGetValueByKeyByHandle(pB, 'Name');
            CoopFitText(misText, misMaxW);
            GUIListBoxAddItem(elmComboMis, misText, ci);
            if (ci=gMap.battleind) then
            coopSelMis := GetGUIListBoxItemsCount(elmComboMis)-1;
         end;
      end;
   end;
   if (coopCampaign='coopsaves') then
   begin
      // all saves of this profile, coop saves first; item tag -(save index+1)
      UserProfileRefreshSavesList;
      var si : Integer;
      for si:=0 to UserGetProfileSavesCount-1 do
      begin
         var saveName : String = UserGetProfileSaveByIndex(si);
         var bCoopFile : Boolean = (SubStr(saveName, 1, 5)='coop_');
         begin
            if (bCoopEdit) and (SubStr(gMap.battlemap, 1, 9)<>'coopsave:') then
            begin
               gMap.battlemap := 'coopsave:'+saveName;
               gbool_gui_senddatasyncparser := True;
            end;
            var saveText : String = saveName;
            if (bCoopFile) then
            saveText := SubStr(saveName, 6, StrLength(saveName)-5);
            CoopFitText(saveText, misMaxW);
            GUIListBoxAddItem(elmComboMis, saveText, -(si+1));
            if (gMap.battlemap='coopsave:'+saveName) then
            coopSelMis := GetGUIListBoxItemsCount(elmComboMis)-1;
         end;
      end;
      if (GetGUIListBoxItemsCount(elmComboMis)=0) then
      GUIListBoxAddItem(elmComboMis, '-', -999999);

   end;
   SetGUIListBoxItemIndexSilent(elmComboCamp, coopSelCamp);
   SetGUIListBoxItemIndexSilent(elmComboMis, coopSelMis);
end
else
begin
   // a normal room after a coop one: back to the plain battle chooser
   SetGUIElementVisible(elmComboChooseMap, True);
   elmCoopOld := GetGUIElementIndexByNameParent('campchooser', elmHnd);
   if (elmCoopOld<>0) then
   RemoveGUIElement(elmCoopOld);
   elmCoopOld := GetGUIElementIndexByNameParent('missionchooser', elmPreviewHnd);
   if (elmCoopOld<>0) then
   RemoveGUIElement(elmCoopOld);
end;"""),
    ("var elmDescriptionScroller : Integer = _gui_CreateScrollLayer(", """// CoopCampaigns: text box height in whole lines with a margin to the frame, so no half line shows
var coopTextH : Integer;
if (bCoop) then
begin
   var coopLine : String = 'A';
   var coopLineH : Integer = _gui_GetFontTextHeight(coopLine, gc_font_serif_11);
   coopTextH := GetGUIElementHeight(descriptionparent)-coopDescrOff-12;
   if (coopLineH>0) then
   coopTextH := (coopTextH div coopLineH)*coopLineH;
end;
var elmDescriptionScroller : Integer = _gui_CreateScrollLayer("""),
    ("descriptionparent, gc_halParentLeft, gc_valParentTop, 0, 2, GetGUIElementWidth(elmHnd), GetGUIElementHeight(elmHnd)-2,",
     "descriptionparent, gc_halParentLeft, gc_valParentTop, 0, 2+coopDescrOff, GetGUIElementWidth(elmHnd), _misc_SwitchInt(GetGUIElementHeight(elmHnd)-2, coopTextH, bCoop),"),
    # the scrollbar is placed inside the (already shifted) text layer: in coop fit it to the
    # box the text is really in (the preview box, shorter than the description box)
    ("var bNeedScroll : Boolean = descrheight>GetGUIElementHeight(elmHnd);",
     "var bNeedScroll : Boolean = descrheight>_misc_SwitchInt(GetGUIElementHeight(elmHnd), coopTextH, bCoop);"),
    ("gc_halParentRightWidth, gc_valParentTop, 0, 20, 0, GetGUIElementHeight(elmHnd), bNeedScroll);",
     "gc_halParentRightWidth, gc_valParentTop, 0, 20, 0, _misc_SwitchInt(GetGUIElementHeight(elmHnd), coopTextH, bCoop), bNeedScroll);"),
    ("if (bOnline and bServer and (gMap.battlestage<>1)) then\nbegin\n   tagstagebtn := cTagGoPickPositions;",
     """// CoopCampaigns: no position stage, the host starts directly (also alone)
if (bCoop and bOnline and bServer) then
begin
   tagstagebtn := cTagStartGame;
   text := GetLocaleTableListItemByID('gui', 'menu.btn.historicalbattle.startgame');
end
else
if (bCoop and bOnline and _net_IsClient) then
begin
   tagstagebtn := cTagReadyGame;
   text := cTextReady;
end
else
if (bOnline and bServer and (gMap.battlestage<>1)) then
begin
   tagstagebtn := cTagGoPickPositions;"""),
    ("if (gMap.battlestage=0) then\nbegin\n   SetGUIElementUserBlend(elmPosText, 0.5);",
     """if (bCoop) then
begin
   // positions, teams and colors do not apply: everyone plays the campaign nation
   var hi, hElm : Integer;
   for hi:=0 to gc_MaxNonSpectatorsCount-1 do
   begin
      hElm := GetGUIElementIndexByNameParent('position|'+IntToStr(hi), elmMain);
      if (hElm<>0) then SetGUIElementVisible(hElm, False);
      hElm := GetGUIElementIndexByNameParent('team|'+IntToStr(hi), elmMain);
      if (hElm<>0) then SetGUIElementVisible(hElm, False);
      hElm := GetGUIElementIndexByNameParent('color|'+IntToStr(hi), elmMain);
      if (hElm<>0) then SetGUIElementVisible(hElm, False);
   end;
end;
SetGUIElementVisible(elmPosText, not bCoop);
SetGUIElementVisible(elmTeamText, not bCoop);
SetGUIElementVisible(elmColorText, not bCoop);

if (gMap.battlestage=0) then
begin
   SetGUIElementUserBlend(elmPosText, 0.5);"""),
]

EVENT_BATTLE_PATCH = [
    ("      cTagGoPickPositions : begin", """      1151 : begin
         // CoopCampaigns: "Load save" / "Campaigns" button of the coop room
         var pCoopBattles : Integer = _parser_ParserBattles(False);
         var pCoopCur : Integer = ParserSelectByHandleByIndex(pCoopBattles, gMap.battleind);
         var bToSaves : Boolean = (pCoopCur=0) or (ParserGetValueByKeyByHandle(pCoopCur, 'Campaign')<>'coopsaves');
         var ci : Integer;
         for ci:=0 to ParserGetCountByHandle(pCoopBattles)-1 do
         begin
            var pB : Integer = ParserSelectByHandleByIndex(pCoopBattles, ci);
            if ParserGetBoolValueByKeyByHandle(pB, 'Coop') then
            if ((bToSaves) and (ParserGetValueByKeyByHandle(pB, 'Campaign')='coopsaves'))
            or ((not bToSaves) and IsFileExists(ParserGetValueByKeyByHandle(pB, 'MapPath'))) then
            begin
               gMap.battleind := ci;
               gMap.battlemap := ParserGetValueByKeyByHandle(pB, 'MapPath');
               break;
            end;
         end;
         gbool_gui_senddatasyncparser := True;
         ExecuteState(showstate);
      end;
      cTagGoPickPositions : begin"""),
    ("      cTagLeaveRoom, cTagCloseRoom : begin", """      cTagLeaveRoom, cTagCloseRoom : begin
         if (ParserSelectByKey('coopcampaigns')<>0) then
         ParserSetIntValueByKeyByHandle(ParserSelectByKey('coopcampaigns'), 'room', 0);"""),
    ("      'mapchooser' : begin", """      'campchooser' : begin
         gMap.battleind := tag;
         ExecuteState(showstate);
      end;
      'missionchooser' : begin
         // tag<0: a coop save of the "Saved games" list, else a battles.cfg index
         if (tag=-999999) then
         exit;
         if (tag<0) then
         begin
            gMap.battlemap := 'coopsave:'+UserGetProfileSaveByIndex(-tag-1);
            gbool_gui_senddatasyncparser := True;
         end
         else
         gMap.battleind := tag;
         ExecuteState(showstate);
      end;
      'mapchooser' : begin"""),
    ("               // prepare gMap players order to correspond selected teams and pos",
     """               // CoopCampaigns: all humans control the campaign nation, put them in team 1
               var pCoopB : Integer = ParserSelectByHandleByIndex(_parser_ParserBattles(False), gMap.battleind);
               if (pCoopB<>0) and ParserGetBoolValueByKeyByHandle(pCoopB, 'Coop') then
               begin
                  var ci : Integer;
                  for ci:=0 to gc_MaxPlayerCount-1 do
                  if (gMap.players[ci].lanid<>0) then
                  begin
                     gMap.players[ci].team := 1;
                     gMap.players[ci].cid := gc_MaxCountryCount;
                  end;
               end;
               // prepare gMap players order to correspond selected teams and pos"""),
]


# After login the multiplayer lobby opens; for the Co-op button open the create-room dialog at once
# (the dialog shows "Co-op" and the room name gets the "[Coop] " prefix).
COOP_ROOM_PREFIX = "[Coop] "

SHOW_SHELL_PATCH = [("_gui_InvokeMouseMove;", """_gui_InvokeMouseMove;

// CoopCampaigns: Co-op button from the main menu -> open the coop create-room dialog right away
var pCoopAuto : Integer = ParserSelectByKey('coopcampaigns');
if (pCoopAuto<>0) and (ParserGetIntValueByKeyByHandle(pCoopAuto, 'autocreate')=1) and (gInternetShell.currentsessionid<=0) and (not gbool_modsyncparserwait) then
begin
   ParserSetIntValueByKeyByHandle(pCoopAuto, 'autocreate', 0);
   ParserSetIntValueByKeyByHandle(pCoopAuto, 'createdlg', 1);
   gInternetShell.bjoin := False;
   gInternetShell.bprivate := False;
   gInternetShell.bratingroom := False;
   gInternetShell.bhistoricalbattle := True;
   ExecuteState('ShowCreateJoinRoom');
end;""")]

# Create-room dialog in coop mode: "Co-op" header, profile name as default room name,
# stays a historical battle room even without the DLC checkbox.
SHOW_CREATE_ROOM_PATCH = [
    ("if (gInternetShell.bjoin) then\nbegin\n   elmHnd := _gui_CreateTextShadowed('thead'", """// CoopCampaigns: coop create-room dialog
var pCoopDlg : Integer = ParserSelectByKey('coopcampaigns');
var bCoopDlg : Boolean = (pCoopDlg<>0) and (ParserGetIntValueByKeyByHandle(pCoopDlg, 'createdlg')=1) and (not gInternetShell.bjoin);
if (bCoopDlg) then
begin
""" + "\n".join("   " + l for l in localized("cTextCoopHeader", "coop").split("\n")) + """
   cTextHeaderCreateRoom := cTextCoopHeader;
   gInternetShell.bhistoricalbattle := True;
   if (gamename='') then
   gamename := UserGetProfileName;
end;
if (gInternetShell.bjoin) then
begin
   elmHnd := _gui_CreateTextShadowed('thead'"""),
    ("   else\n   if (gInternetShell.bhistoricalbattle) then\n   gInternetShell.bhistoricalbattle := False;",
     "   else\n   if (gInternetShell.bhistoricalbattle) and (not bCoopDlg) then\n   gInternetShell.bhistoricalbattle := False;"),
]

# Create: prefix the room name; Cancel/Esc: drop the coop intent so the next room is a normal one.
COOP_DLG_CANCEL = """var pCoopCancel : Integer = ParserSelectByKey('coopcampaigns');
if (pCoopCancel<>0) and (ParserGetIntValueByKeyByHandle(pCoopCancel, 'createdlg')=1) then
begin
   ParserSetIntValueByKeyByHandle(pCoopCancel, 'createdlg', 0);
   ParserSetIntValueByKeyByHandle(pCoopCancel, 'intent', 0);
   gInternetShell.bhistoricalbattle := False;
end;"""

EVENT_CREATE_ROOM_PATCH = [
    ("            cEscChar : begin\n", "            cEscChar : begin\n" + "\n".join("               " + l for l in COOP_DLG_CANCEL.split("\n")) + "\n"),
    ("      gc_gui_event_tagClose, gc_gui_hotkey_btnEscape : begin\n",
     "      gc_gui_event_tagClose, gc_gui_hotkey_btnEscape : begin\n" + "\n".join("         " + l for l in COOP_DLG_CANCEL.replace("pCoopCancel", "pCoopClose").split("\n")) + "\n"),
    ("            if (gamename<>'') then\n            begin\n", """            if (gamename<>'') then
            begin
               // CoopCampaigns: coop room name always starts with the coop mark
               var pCoopCreate : Integer = ParserSelectByKey('coopcampaigns');
               if (pCoopCreate<>0) and (ParserGetIntValueByKeyByHandle(pCoopCreate, 'createdlg')=1) then
               begin
                  ParserSetIntValueByKeyByHandle(pCoopCreate, 'createdlg', 0);
                  gInternetShell.bhistoricalbattle := True;
                  if (SubStr(gamename, 1, """ + str(len(COOP_ROOM_PREFIX)) + """)<>'""" + COOP_ROOM_PREFIX + """') then
                  gamename := '""" + COOP_ROOM_PREFIX + """'+gamename;
                  Log('CoopCampaigns: coop room created: '+gamename);
               end;
"""),
]


# In-game player list: a battle shows only lobby humans; a coop mission must list the mission's
# players by their mission names, like the campaign does.
EDITOR_INFO_PATCH = [
    ("var teamsActive : array [0..gc_MaxPlayerCount] of Integer;", """// CoopCampaigns: coop mission is listed like a campaign
var bCoopInfo : Boolean;
if (gMap.bbattle) then
begin
   var pCoopInfo : Integer = ParserSelectByHandleByIndex(_parser_ParserBattles(False), gMap.battleind);
   bCoopInfo := (pCoopInfo<>0) and ParserGetBoolValueByKeyByHandle(pCoopInfo, 'Coop');
end;
var teamsActive : array [0..gc_MaxPlayerCount] of Integer;"""),
    ("         and ((not gMap.bbattle) or (gMap.players[pl].lanid<>0)) then",
     "         and ((not gMap.bbattle) or (bCoopInfo) or (gMap.players[pl].lanid<>0)) then"),
    ("\n            if (gScenario.bexists) and (not gMap.bbattle) then",
     "\n            if (gScenario.bexists) and ((not gMap.bbattle) or (bCoopInfo)) then"),
    ("\n               if (gScenario.bexists) and (not gMap.bbattle) then",
     "\n               if (gScenario.bexists) and ((not gMap.bbattle) or (bCoopInfo)) then"),
]


SHOW_BATTLE_PATCH += [
    # coop room: one more bottom button, a bit closer together so four fit
    ("var btncount : Integer = _misc_SwitchInt(2, 3, bOnline);\nconst btnwidth = 268;",
     "var btncount : Integer = _misc_SwitchInt(2, 3, bOnline);\nvar btnwidth : Integer = 268;\n"
     "if (bCoop) then\nbegin\n   btncount := btncount+1;\n   btnwidth := 236;\nend;"),
    ("posX+btnwidth*2, posY, 0, 0, eventstate, nul, cTagCloseRoom, bupdate);",
     "posX+btnwidth*_misc_SwitchInt(2, 3, bCoop), posY, 0, 0, eventstate, nul, cTagCloseRoom, bupdate);"),
    # window title: "Co-op" instead of "Historical battle"
    ("var cTextHeader : String = GetLocaleTableListItemByID('gui', 'menu.btn.historicalbattle');",
     "var cTextHeader : String = GetLocaleTableListItemByID('gui', 'menu.btn.historicalbattle');\n"
     "if (bCoop) then\nbegin\n" + localized("cTextCoopHeader", "coop") + "\ncTextHeader := cTextCoopHeader;\nend;"),
    # bottom of the right column: "Load save" button switches the room to the coop saves
    # (and back to the campaigns)
    ("var tagstagebtn : Integer;", """// CoopCampaigns: "Load save" / "Campaigns" button, third in the bottom row
const cTagCoopSaves = 1151;
var elmCoopBtn : Integer = GetGUIElementIndexByNameParent('bcoopsaves', elmMain);
if (elmCoopBtn<>0) then
RemoveGUIElement(elmCoopBtn);
if (bCoop) then
begin
""" + localized("cTextCoopLoad", "loadsave") + "\n" + localized("cTextCoopBack", "tocampaigns") + """
   var coopBtnText : String = cTextCoopLoad;
   if (coopCampaign='coopsaves') then
   coopBtnText := cTextCoopBack;
   elmCoopBtn := _gui_CreateButton('bcoopsaves', elmMain, 'btn.medium', gc_halParentMiddle, gc_valParentBottomHeight, posX+btnwidth*2, posY, 0, 0, eventstate, nul, cTagCoopSaves, bupdate);
   _gui_CreateText('t1', elmCoopBtn, coopBtnText, gc_halParentMiddle, gc_valParentMiddle, 0, 0, 0, 0, gc_halLeft, gc_valMiddle, gc_font_serif_12, color, bupdate);
   var bCoopBtnOn : Boolean = (bServer or bOffline) and (gMap.battlestage=0);
   SetGUIElementEnabled(elmCoopBtn, bCoopBtnOn);
   SetGUIElementUserBlend(elmCoopBtn, _misc_SwitchFloat(0.7, 1, bCoopBtnOn));
end;

var tagstagebtn : Integer;"""),
    # the saves pseudo-campaign keeps the save chosen by the host in battlemap
    ("         gMap.battlemap := mapPath;",
     "         if (ParserGetValueByKeyByHandle(pBattle, 'Campaign')<>'coopsaves') then\n         gMap.battlemap := mapPath;"),
    ("activedescription := GetLocaleTableListItemByID('missions', activedescription);",
     "activedescription := GetLocaleTableListItemByID('missions', activedescription);\n"
     "if (bCoop) and (coopCampaign='coopsaves') then\nbegin\n" + localized("cTextCoopSaveHint", "savehint") +
     "\nactivedescription := cTextCoopSaveHint;\nend;"),
    # no save chosen yet: nothing to start
    ("SetGUIElementEnabled(elmHnd, benabled);",
     "if (bCoop) and (coopCampaign='coopsaves') and (SubStr(gMap.battlemap, 1, 9)<>'coopsave:') then\n"
     "benabled := False;\nSetGUIElementEnabled(elmHnd, benabled);"),
]

# Saving in a coop game: every PC saves the same paused moment under the same name
# (vanilla WriteSave/ReadSave network save, used by its multiplayer autosave).
SHOW_MENU_PATCH_INGAME = [
    ("elmHnd := ShowButton('bsavegame'", """// CoopCampaigns: the host of a coop mission may save online
var bCoopSave : Boolean;
if (_net_IsOnline) and (_net_IsServer) and (gMap.bbattle) then
begin
   var pCoopMenu : Integer = ParserSelectByHandleByIndex(_parser_ParserBattles(False), gMap.battleind);
   bCoopSave := (pCoopMenu<>0) and ParserGetBoolValueByKeyByHandle(pCoopMenu, 'Coop');
end;
elmHnd := ShowButton('bsavegame'"""),
    ("(gPlayer[plind].victorystate=gc_player_victorystate_none) and (not _net_IsOnline));",
     "(gPlayer[plind].victorystate=gc_player_victorystate_none) and ((not _net_IsOnline) or (bCoopSave)));"),
]

MODAL_PATCH = [
    ("                     UserProfileSaveMap(gstring_gui_saveload_name);", """                     if (_net_IsOnline) then
                     begin
                        // CoopCampaigns: coop save on all PCs at the same (paused) moment
                        if (not GetProgressingPauseMode) then
                        begin
""" + "\n".join("                           " + l for l in localized("cTextCoopPause", "pause").split("\n")) + """
                           var coopEmpty : String;
                           _misc_ShowLogMessage(gc_logmessagesid_custom, cTextCoopPause, coopEmpty, False);
                        end
                        else
                        begin
                           var coopSaveName : String = gstring_gui_saveload_name;
                           if (SubStr(coopSaveName, 1, 5)<>'coop_') then
                           coopSaveName := 'coop_'+coopSaveName;
                           var pCoopSave : Integer = ParserSelectByKey('coopcampaigns');
                           if (pCoopSave=0) then
                           pCoopSave := ParserCreate('coopcampaigns');
                           ParserSetValueByKeyByHandle(pCoopSave, 'savename', coopSaveName);
                           ExecuteState('WriteSave');
                           UserProfileSaveMap(coopSaveName);
                        end;
                     end
                     else
                     UserProfileSaveMap(gstring_gui_saveload_name);"""),
]

WRITE_SAVE_PATCH = [
    ("   RecordCustomWriteString('autosave');", """   // CoopCampaigns: a named coop save instead of the network autosave
   var coopSaveName : String = 'autosave';
   var pCoopSave : Integer = ParserSelectByKey('coopcampaigns');
   if (pCoopSave<>0) and (ParserGetValueByKeyByHandle(pCoopSave, 'savename')<>'') then
   begin
      coopSaveName := ParserGetValueByKeyByHandle(pCoopSave, 'savename');
      ParserSetValueByKeyByHandle(pCoopSave, 'savename', '');
   end;
   RecordCustomWriteString(coopSaveName);"""),
]

READ_SAVE_PATCH = [("   UserProfileSaveMap(mapname);", "   UserProfileSaveMap(mapname);\n   UserProfileRefreshSavesList;")]

# Starting a coop room on a save: every PC loads its own copy of the save with that name.
NEW_GAME_PATCH = [("   LoadMapNeeded(gMap.battlemap);", """   var coopMapPath : String = gMap.battlemap;
   if (SubStr(coopMapPath, 1, 9)='coopsave:') then
   begin
      // CoopCampaigns: continue a coop save; the file lives in each player's own profile
      var coopSaveName : String = SubStr(coopMapPath, 10, StrLength(coopMapPath)-9);
      coopMapPath := '';
      UserProfileRefreshSavesList;
      var si : Integer;
      for si:=0 to UserGetProfileSavesCount-1 do
      if (UserGetProfileSaveByIndex(si)=coopSaveName) then
      coopMapPath := UserGetProfileSaveFilePathByIndex(si);
      if (coopMapPath='') then
      ErrorLog('CoopCampaigns: save not found on this PC: '+coopSaveName)
      else
      Log('CoopCampaigns: continue save '+coopMapPath);
   end;
   LoadMapNeeded(coopMapPath);""")]

# After the save is loaded: keep the saved world, take only the network session from the lobby.
LOAD_FULL_PATCH = [("if (gMapHistoricalBattle.bbattle) and (gMapHistoricalBattle.battlemap<>'') then", """if (gMapHistoricalBattle.bbattle) and (SubStr(gMapHistoricalBattle.battlemap, 1, 9)='coopsave:') then
begin
   // CoopCampaigns: continuing a coop save (no mission start scripts, no lobby teams)
   var ci : Integer;
   for ci:=0 to gc_MaxPlayerCount-1 do
   begin
      gMap.players[ci].lanid := gMapHistoricalBattle.players[ci].lanid;
      gMap.players[ci].bready := gMapHistoricalBattle.players[ci].bready;
      gMap.players[ci].bloaded := gMapHistoricalBattle.players[ci].bloaded;
      gMap.players[ci].bleave := gMapHistoricalBattle.players[ci].bleave;
      if (ci>0) then
      gMap.players[ci].bhuman := (gMapHistoricalBattle.players[ci].lanid<>0);
   end;
   gMap.players[0].name := gMapHistoricalBattle.players[0].name;
   gMap.gamestage := gMapHistoricalBattle.gamestage;
   _control_SetPlayer(0);
   Log('CoopCampaigns: save continued');
   var coopClean : TMap;
   gMapHistoricalBattle := coopClean;
end
else
if (gMapHistoricalBattle.bbattle) and (gMapHistoricalBattle.battlemap<>'') then""")]


# Joining rooms of players without the mod. Vanilla rooms carry "type char + game checksum"
# (5 chars); with script mods the game marks them "needs mod sync" and drops the connection.
# This mod changes only menus and coop missions (vanilla battles.cfg indices are kept), so such
# rooms are joinable as they are.
LAN_EVENT_PATCH = [("      checksumres := gc_result_roomchecksum_nomod;\n", """      checksumres := gc_result_roomchecksum_nomod;
      // CoopCampaigns: a room without script mods is compatible with this mod
      if (StrLength(gstring_roomchecksum)=5) and (checksumres=gc_result_roomchecksum_modneedsync) then
      begin
         checksumres := gc_result_roomchecksum_modok;
         Log('CoopCampaigns: joining a room without mods: '+gstring_roomchecksum);
      end;
""")]


def build_scripts():
    patch_inc(("scripts", "common.inc", "initmaphistoricalbattle.inc"), PATCHES)
    renaissance = "gOnlineResumeCreateMode" in read(base_file("scripts", "dmscript.global"))
    patch_inc(("gui", "menu.inc", "showmainmenu.inc"), SHOW_MENU_PATCH)
    patch_inc(("gui", "menu.inc", "eventmainmenu.inc"), event_menu_patch(renaissance))
    patch_inc(("gui", "menu.inc", "showhistoricalbattle.inc"), SHOW_BATTLE_PATCH)
    patch_inc(("gui", "menu.inc", "eventhistoricalbattle.inc"), EVENT_BATTLE_PATCH)
    patch_inc(("gui", "menu.inc", "showinternetshell.inc"), SHOW_SHELL_PATCH)
    patch_inc(("gui", "menu.inc", "showcreatejoinroom.inc"), SHOW_CREATE_ROOM_PATCH)
    patch_inc(("gui", "menu.inc", "eventcreatejoinroom.inc"), EVENT_CREATE_ROOM_PATCH)
    patch_inc(("gui", "menu.inc", "showeditorinfo.inc"), EDITOR_INFO_PATCH)
    patch_inc(("gui", "menu.inc", "showmenu.inc"), SHOW_MENU_PATCH_INGAME)
    patch_inc(("gui", "menu.inc", "eventmodalmessage.inc"), MODAL_PATCH)
    patch_inc(("gui", "menu.inc", "writesave.inc"), WRITE_SAVE_PATCH)
    patch_inc(("gui", "menu.inc", "readsave.inc"), READ_SAVE_PATCH)
    patch_inc(("gui", "menu.inc", "donewgame.inc"), NEW_GAME_PATCH)
    patch_inc(("gui", "menu.inc", "onlanevent.inc"), LAN_EVENT_PATCH)
    onloadfull = ("scripts", "common.inc", "onloadfull.inc")
    patches = list(LOAD_FULL_PATCH)
    if "_resume_AfterLoad;" in read(base_file(*onloadfull)):
        patches += RESUME_PATCH
    patch_inc(onloadfull, patches)


if __name__ == "__main__":
    BASE = None
    if "--base" in sys.argv:
        BASE = sys.argv[sys.argv.index("--base") + 1]
        print("base mod:", BASE)
    build_battles()
    build_scripts()
