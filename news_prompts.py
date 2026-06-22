"""Prompt templates for the Guildhall AI news desk.

The news desk turns dry realm data into in-universe newspaper articles. There are
two kinds of story:

  * Market stories explain a shift in the Auction House -- the daily buffs rolled
    by the AHPricingService (see ``AHPricingService/dailyevent.py`` and the dict
    returned by ``ahservice.events()``). Each of the three buffs gets its own
    section/category:
        - Professional Digest -> the profession-of-the-day buff
        - Gear For You        -> the category-of-the-day buff
        - Primary Stats       -> the stat-of-the-day buff
  * Heroic Exploits stories celebrate a player's recent doings -- achievements
    earned and quests completed.

Each category carries a small roster of "reporters", each a fixed byline with its
own voice. Generation picks a reporter, fills its template with the day's data,
and asks the model (Gemini, see ``gemini_api.py``) for a JSON article. This module
owns ONLY the prompt text and the reporter personas -- no model calls, no DB, no
Flask. That keeps the voices easy to read and tweak in one place.

Every article comes back as a JSON object::

    {"headline": str, "dek": str, "content": str}

The author is NOT taken from the model output -- we already know which reporter we
asked, so the byline is authoritative from ``Reporter.byline``.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field


# --- categories --------------------------------------------------------------
# The four top-level sections. The three market sections each bind to one field
# of the daily-events dict; Heroic Exploits is fed player activity instead.
HEROIC_EXPLOITS = "heroic_exploits"
OBITUARIES = "obituaries"
PROFESSIONAL_DIGEST = "professional_digest"
GEAR_FOR_YOU = "gear_for_you"
PRIMARY_STATS = "primary_stats"

CATEGORY_TITLES = {
    HEROIC_EXPLOITS: "Heroic Exploits",
    OBITUARIES: "Obituaries",
    PROFESSIONAL_DIGEST: "Professional Digest",
    GEAR_FOR_YOU: "Gear For You",
    PRIMARY_STATS: "Stats: A Primary Concern",
}

# Which market sections read which buff out of ahservice.events(). Heroic Exploits
# is intentionally absent -- it isn't a market story.
MARKET_BUFF_FIELD = {
    PROFESSIONAL_DIGEST: ("profession", "profession_multiplier"),
    GEAR_FOR_YOU: ("category", "category_multiplier"),
    PRIMARY_STATS: ("stat", "stat_multiplier"),
}

# The mirror-image slump fields each market section also reports (the
# profession/category/stat in oversupply, trading at a discount today). Same
# axis as the buff above, so each story becomes a winners-and-losers piece.
MARKET_SLUMP_FIELD = {
    PROFESSIONAL_DIGEST: ("discount_profession", "discount_profession_multiplier"),
    GEAR_FOR_YOU: ("discount_category", "discount_category_multiplier"),
    PRIMARY_STATS: ("discount_stat", "discount_stat_multiplier"),
}


# --- reporters ---------------------------------------------------------------
@dataclass(frozen=True)
class Reporter:
    """A fixed byline with its own voice.

    ``byline`` is the author printed on the article. ``title`` is the reporter's
    beat/role (flavor for the byline line). ``persona`` is WHO they are -- a
    second-person biography (person, race, where they live, their background) so
    the model writes as a real character, not a style. ``voice`` is HOW they write
    (style/diction); ``tendency`` is their composition lean (long-winded / terse /
    elegant ...) nudging where, inside the length the subject warrants, their copy
    lands.
    """

    key: str
    byline: str
    title: str
    voice: str
    persona: str = ""   # second-person bio: who they are + where + background
    tendency: str = ""  # length/composition lean, e.g. "terse", "long-winded"
    dateline: str = ""  # the town the reporter files from, e.g. "STORMWIND"


# Rosters keyed by category. A few voices each so the same buff doesn't read the
# same way two days running -- generation picks one (see ``pick_reporter``).
ROSTERS: dict[str, list[Reporter]] = {
    PROFESSIONAL_DIGEST: [
        Reporter(
            key="cogsworth",
            byline="Gristlewick Cogsworth",
            title="Trade Ledger Analyst",
            persona="You are a gnome of Ironforge, a former Tinkers' Union "
                    "actuary who keeps every auction price-swing in a leather "
                    "ledger chained to your belt. You live in cramped rooms above "
                    "a clockmaker's shop in the Commons and trust numbers over "
                    "people.",
            tendency="Terse and clipped -- leads with the figure and stops; "
                     "won't spend ten words where three will do.",
            dateline="IRONFORGE",
            voice=(
                "Dry, precise, faintly smug about the forecasting. Lead with the "
                "figure and treat a price swing like a weather front; can't "
                "resist a parenthetical aside. Never gush -- let the multiplier "
                "speak -- and work in one concrete crafting-supply detail."
            ),
        ),
        Reporter(
            key="goldsign",
            byline="Madame Henrietta Goldsign",
            title="Merchant-Guild Correspondent",
            persona="You are a human widow of a Stormwind trade-baron, living "
                    "comfortably in a townhouse in the Trade District. You married "
                    "into the merchant guilds decades ago and have never left "
                    "their salons, where you know every artisan and every scandal.",
            tendency="Long-winded and digressive -- savours an aside and a "
                     "flourish, and runs to the fuller end of any length.",
            dateline="STORMWIND",
            voice=(
                "Warm, arch and gossipy. Cover the trade guilds like a royal "
                "court; name-drop artisans and auctioneers as though they were "
                "nobility. Frame a profession's good day as a scandalous bit of "
                "fortune everyone is whispering about over wine."
            ),
        ),
        Reporter(
            key="ironledger",
            byline="Brokk Ironledger",
            title="Foundry Beat Reporter",
            persona="You are a dwarf of Ironforge who spent thirty years at the "
                    "Great Forge until your lungs gave out from the smoke. You "
                    "live in the Military Ward, still take your ale with the "
                    "smiths each evening, and write about honest work.",
            tendency="Blunt and economical -- short, hammered sentences, no "
                     "ornament.",
            dateline="IRONFORGE",
            voice=(
                "Gruff and grounded. Short, blunt sentences. Respect honest work "
                "and a fair price; be suspicious of a spike that's too good. Quote "
                "the crafters at the forge, never the buyers."
            ),
        ),
    ],
    GEAR_FOR_YOU: [
        Reporter(
            key="brightblade",
            byline="Sera Brightblade",
            title="Outfitting Columnist",
            persona="You are a young human adventurer-turned-columnist who blew a "
                    "small fortune on gear before you learned to spot real value. "
                    "You live in a rented loft in Dalaran and treat outfitting as "
                    "high fashion.",
            tendency="Effusive and breathless -- piles on enthusiasm and tends "
                     "to run a little long.",
            dateline="DALARAN",
            voice=(
                "Breathless and high-energy. Treat today's hot item category like "
                "the must-have look of the season. Heavy on second-person address "
                "to the reader ('your shoulders deserve this'), but always land "
                "one genuinely practical buying tip."
            ),
        ),
        Reporter(
            key="mendel",
            byline="Old Tobias Mendel",
            title="Armorer's Bench Veteran",
            persona="You are an aging human armorer in Stormwind's Dwarven "
                    "District, forty years at the bench under three masters. You "
                    "live behind your little shop, trust your hands over any fad, "
                    "and have outfitted more adventurers than you can recall.",
            tendency="Plain and unhurried -- folksy and measured, neither "
                     "padded nor clipped.",
            dateline="STORMWIND",
            voice=(
                "Practical, folksy and a bit world-weary; distrust fads. Tell the "
                "reader what a category is actually good for and who should "
                "bother, through a craftsman's eye, with the odd grumble about "
                "'kids these days'."
            ),
        ),
        Reporter(
            key="sparkwhistle",
            byline="Zinge Sparkwhistle",
            title="Bargain Hunter",
            persona="You are a goblin hustler working the docks of Booty Bay out "
                    "of a stall you 'inherited.' You live wherever the next deal "
                    "is, owe money in three ports, and can smell a markup from a "
                    "ship's length away.",
            tendency="Punchy and rapid-fire -- short urgent bursts, never "
                     "lingers.",
            dateline="BOOTY BAY",
            voice=(
                "A relentless hard sell. Fast, punchy, exclamation marks, all "
                "urgency ('won't last, friend!'). Quote a price like a carnival "
                "barker. Fun and slightly untrustworthy, but the underlying tip "
                "is real."
            ),
        ),
    ],
    PRIMARY_STATS: [
        Reporter(
            key="elandrin",
            byline="Magister Elandrin",
            title="Arcane Market Theorist",
            persona="You are a blood elf magister of the Kirin Tor who lectures "
                    "on the economics of enchantment from a study in Dalaran's "
                    "Violet Citadel. You treat a price swing as an arcane "
                    "phenomenon to be modelled and explained.",
            tendency="Elegant and precise -- balanced, well-turned sentences "
                     "with a scholar's economy.",
            dateline="DALARAN",
            voice=(
                "Measured, lightly academic, fond of an analogy. Treat a stat's "
                "price swing as a phenomenon to be analysed; explain WHO wants the "
                "stat and why the demand makes sense. Never hype."
            ),
        ),
        Reporter(
            key="stonefist",
            byline="Coach Bromm Stonefist",
            title="Combat Conditioning Columnist",
            persona="You are an orc veteran of the Valley of Strength who runs a "
                    "training pit in Orgrimmar. You live by the forge-heat, coach "
                    "young grunts on what to wear and wield, and see every stat as "
                    "a muscle to be trained.",
            tendency="Terse and barked -- clipped imperatives, minimal words.",
            dateline="ORGRIMMAR",
            voice=(
                "All motivation, drill-instructor energy. Talk about the stat of "
                "the day like it's leg day. Loud, encouraging, short imperatives "
                "('Stack it. Wear it. Win.'). Strongest on the physical stats but "
                "game for any."
            ),
        ),
        Reporter(
            key="whisper",
            byline="'Whisper'",
            title="Anonymous Trade-Floor Source",
            persona="You are an unnamed informant who haunts the trade tents of "
                    "Everlook in Winterspring. No one knows your face or your "
                    "race; you deal in secrets as readily as in goods, and you "
                    "never sign your true name.",
            tendency="Spare and cryptic -- says little, in low confidences; the "
                     "briefest of them all.",
            dateline="EVERLOOK",
            voice=(
                "Cryptic and shadowy; speak in low confidences. Short, "
                "second-person. Frame the stat buff as inside knowledge the reader "
                "wasn't supposed to have. Mysterious, but the advice is sound."
            ),
        ),
    ],
    HEROIC_EXPLOITS: [
        Reporter(
            key="songweaver",
            byline="Lyrabel Songweaver",
            title="Wandering Balladeer",
            persona="You are a night elf wandering minstrel who lodges at the "
                    "Lion's Pride Inn in Goldshire between roads. You have sung in "
                    "a hundred taverns from Darnassus to Booty Bay and remember "
                    "every hero's name and deed.",
            tendency="Grand and long-winded -- reaches for sweep and ornament, "
                     "and writes to the fullest length the tale will bear.",
            dateline="GOLDSHIRE",
            voice=(
                "Render deeds as the stuff of legend -- grand, warm, a little "
                "overblown, but never mocking. Reach for the heroic register and "
                "the sweep of saga, while keeping the actual facts (the "
                "achievement, the quest, the place) front and centre."
            ),
        ),
        Reporter(
            key="hale",
            byline="Sgt. Marcus Hale (ret.)",
            title="Embedded War Correspondent",
            persona="You are Sergeant Marcus Hale, retired -- thirty years in the "
                    "Stormwind army until Northrend took your leg and, at the "
                    "Wrathgate, nearly every soldier you commanded. A quiet life "
                    "back home would not have you, so now you embed with "
                    "adventurers and file their fights from the thick of it. The "
                    "twist: you have quietly adopted every reckless hero as one of "
                    "your own green recruits, and you write them up like a sergeant "
                    "who will be damned before he loses another.",
            tendency="Immersive and propulsive -- builds a you-are-there scene "
                     "with real momentum; flowing narrative, never clipped "
                     "fragments, and writes to a full, gripping middle length.",
            dateline="THE FRONT",
            voice=(
                "An embedded war correspondent who drops the reader into the thick "
                "of it -- the grit, the noise, the smell of the field -- in "
                "present-tense narrative that carries them along. Quote the "
                "bystanders like comrades sharing a foxhole, draw a hard-won lesson "
                "from your own soldiering days, and leaven the danger with a "
                "veteran's gallows humour. A gripping story, never a list of facts; "
                "deep respect for what the fight cost."
            ),
        ),
        Reporter(
            key="underbottom",
            byline="Pip Underbottom",
            title="Tavern-Tales Columnist",
            persona="You are a rotund, well-liked human regular at the Pig and "
                    "Whistle tavern in Stormwind's Old Town. You have never "
                    "adventured a day in your life, but you know everyone's "
                    "business and hear every tale secondhand over a pint.",
            tendency="Warm and rambling -- meanders through asides, and leans "
                     "long when the story is a good one.",
            dateline="THE PIG AND WHISTLE",
            voice=(
                "Cosy and gossipy, full of warmth and small asides about what the "
                "regulars are saying. Tell it as a tale heard secondhand over a "
                "pint. Celebrate the everyday adventurer and find the heart in "
                "even a modest deed."
            ),
        ),
    ],
    OBITUARIES: [
        Reporter(
            key="aldous",
            byline="Brother Aldous Greymane",
            title="Cathedral Elegist",
            persona="You are a human priest of the Cathedral of Light in "
                    "Stormwind, keeper of its book of the dead. You have read the "
                    "last rites over more fallen adventurers than you can count, "
                    "and you write their elegies by candlelight.",
            tendency="Gentle and even-cadenced -- calm, hymn-like lines, "
                     "unhurried.",
            dateline="STORMWIND",
            voice=(
                "Compose funeral verse with grave dignity and quiet comfort. "
                "Solemn, kind, unhurried -- gentle, hymn-like elegies that honour "
                "the fallen without melodrama, name how they fell plainly, and "
                "find a measure of peace in a life of adventure ended. Calm, "
                "even-cadenced lines; never mocking, never grim for its own sake. "
                "Write ONLY in verse."
            ),
        ),
        Reporter(
            key="morwen",
            byline="Morwen Ashcroft",
            title="Epitaph-Poet of the Rolls of the Dead",
            persona="You are a human archivist in Dalaran who tends the Rolls of "
                    "the Dead. Widowed young, you have given your life to setting "
                    "the fallen down in careful verse, that none be forgotten.",
            tendency="Spare and chiselled -- the tersest verse, every word "
                     "load-bearing.",
            dateline="DALARAN",
            voice=(
                "Measured, elegiac, a little formal -- a true epitaph cadence, "
                "spare and chiselled like words cut in stone. Treat each death as "
                "a closed chapter, recorded accurately and with respect, naming "
                "the foe that claimed them and the deeds that preceded the fall. "
                "Write ONLY in verse."
            ),
        ),
        Reporter(
            key="grimsby",
            byline="Mortimer Grimsby",
            title="Undertaker-Balladeer",
            persona="You are a Forsaken undertaker in Brill, plying your trade "
                    "since not long after you yourself were raised. You have "
                    "buried -- and dug up -- more adventurers than you can count, "
                    "and you mark each one with a graveside rhyme.",
            tendency="Dry and economical -- short rhyming lines with a dark "
                     "turn at the end.",
            dateline="BRILL",
            voice=(
                "Wry and world-weary. Dry gallows humour held in check by genuine "
                "respect; plain, unsentimental verse, matter-of-fact about death, "
                "with the odd dark rhyming aside. Write ONLY in verse."
            ),
        ),
    ],
}


# --- shared world primer -----------------------------------------------------
# Grounding the model in real WotLK (3.3.5a) geography and NPC names keeps the
# articles from inventing fake places. Reporters are told to reference these so
# the copy reads as genuinely Azerothian.
SETTING_PRIMER = """\
You write for the GUILDHALL HERALD, a newspaper read by adventurers of a World of \
Warcraft realm in the Wrath of the Lich King era (patch 3.3.5a). It is a living \
world of bustling cities, trade caravans, and a real Auction House run by goblin \
and gnomish auctioneers.

Ground every article in REAL places and people of this world. Reference at least \
one town and one named NPC from the lists below; never invent place names.

ALLIANCE & NEUTRAL TOWNS: Stormwind City, Ironforge, Darnassus, the Exodar, \
Goldshire, Lakeshire, Menethil Harbor, Theramore Isle, Stonard, Booty Bay, \
Gadgetzan, Everlook, Ratchet, Dalaran, Shattrath City.
HORDE TOWNS: Orgrimmar, Thunder Bluff, Undercity, Silvermoon City, Brill, \
Razor Hill, The Crossroads, Hammerfall, Tarren Mill.
NOTABLE FIGURES: King Varian Wrynn, High Tinker Mekkatorque, Thrall, Cairne \
Bloodhoof, Archmage Rhonin, Lady Jaina Proudmoore, the trade-baron Gallywix.

STYLE RULES:
- Write from where YOU file. Anchor the story on your own home ground -- the town \
in your dateline and the places and folk near it -- and open there. Do NOT default \
to "the North", Northrend, Icecrown, Dalaran or the Lich King unless the deeds you \
are reporting plainly happened there; most do not, so reach for YOUR region first.
- Length and depth are set per story below -- match them. Weight your words by \
how much each deed actually mattered: dwell on the big ones, give a trivial errand \
a passing line or fold it in with others, and never pad a small story to fill space.
- A real newspaper voice -- concrete, specific, never generic filler.
- No modern/out-of-universe words (no "gamers", "players", "patch", "buff", \
"stats", "RNG", "server"). Say "adventurers", "the markets", "this morning".
- Do not break character or mention that you are an AI.
"""

# Auction-house / trade NPCs belong to MARKET stories only -- a hero's exploit or
# an obituary has no business name-dropping auctioneers. Appended to the market
# prompt alone (not the shared primer).
MARKET_NPCS = """\
AUCTION HOUSE & TRADE NPCS you may reference: Auctioneer Fitch and Auctioneer \
Beardo (Stormwind), Auctioneer Golothas (Ironforge), Auctioneer Drezbit and \
Auctioneer Grimful (Orgrimmar), Auctioneer Kresel (Undercity), Banker Hnatala, \
the goblin trade princes of the Steamwheedle Cartel."""


def _output_contract(scale: str = "standard") -> str:
    """The machine-parseable JSON contract, with the body length matched to the
    story's importance ``scale`` (see ``_LENGTH``)."""
    return (
        "Respond with a SINGLE JSON object and nothing else (no markdown fences, "
        "no preamble). Schema:\n"
        "  \"headline\": a punchy newspaper headline, max ~12 words, in the "
        "reporter's voice.\n"
        "  \"dek\": a one-sentence subheading/standfirst that teases the story.\n"
        f"  \"content\": the article body. Separate any paragraphs with "
        "blank lines. Let your own tendency nudge you toward the terser or fuller "
        "end of that length.\n"
        "Do not include a byline or author field -- that is added separately."
    )

# Obituaries are death notices, not articles -- always a brief elegiac poem.
# The verse length scales with the fallen one's standing (their level): a
# seasoned hero earns a fuller elegy, a fledgling a spare couplet.
def _obituary_contract(level: int | None) -> str:
    lvl = int(level or 0)
    if lvl >= 70:
        verse = "an elegiac verse of 4-6 lines"
    elif lvl >= 30:
        verse = "a brief elegiac verse of 3-4 lines"
    else:
        verse = "a spare elegiac verse of 2-3 lines"
    return (
        "Respond with a SINGLE JSON object and nothing else (no markdown fences, "
        "no preamble). Schema:\n"
        "  \"headline\": a brief, dignified title -- the fallen one's name, or a "
        "short epitaph (max ~8 words).\n"
        "  \"dek\": \"\" (leave this empty for an obituary).\n"
        f"  \"content\": ALWAYS a poem -- {verse}, one line per line with a line "
        "break (newline) between each. Never prose, never a paragraph. Spare and "
        "elegiac; nothing longer.\n"
        "Do not include a byline or author field -- that is added separately."
    )


def _reporter_block(reporter: Reporter) -> str:
    """The 'who is writing this' section shared by both builders."""
    home = reporter.dateline.title() if reporter.dateline else ""
    dateline = f" filing from {home}" if home else ""
    vantage = (
        f"You report from {home}: see the story through the eyes of that town and "
        "the folk who pass through it, and open on your own ground.\n"
        if home else "")
    persona = f"{reporter.persona}\n" if reporter.persona else ""
    tendency = (f"YOUR TENDENCY: {reporter.tendency}\n"
                if reporter.tendency else "")
    return (
        f"YOU ARE: {reporter.byline}, {reporter.title} for the Guildhall "
        f"Herald{dateline}.\n"
        f"{persona}"
        f"{vantage}"
        f"YOUR VOICE: {reporter.voice}\n"
        f"{tendency}"
        "Write the whole article as this person, in this voice. Do not imitate any "
        "other reporter."
    )


# --- market stories ----------------------------------------------------------
def _pct(multiplier: float) -> str:
    """A buff multiplier (e.g. 1.35) as plain newspaper language ('up 35%')."""
    delta = round((float(multiplier) - 1.0) * 100)
    return f"up {delta}%" if delta >= 0 else f"down {abs(delta)}%"


def _slump_beat(category: str, subject: str, movement: str) -> str:
    """The 'meanwhile, today's losers' clause woven into each market story when a
    slump is in play -- the same axis as the buff, but trading down."""
    if category == PROFESSIONAL_DIGEST:
        return (
            f"At the same time, the {subject} trade is having a wretched day: its "
            f"wares have slipped {movement} as buyers turn away. Work both sides -- "
            f"the artisans riding high and the {subject} crafters left holding "
            "stock they can't move."
        )
    if category == GEAR_FOR_YOU:
        return (
            f"Meanwhile {subject} have gone cold, {movement} as the crowds look "
            "elsewhere. Note who should sit tight and wait rather than dump them at "
            "a loss this morning."
        )
    if category == PRIMARY_STATS:
        return (
            f"By contrast, {subject} has fallen out of fashion, {movement} at "
            "auction -- explain the swing in taste and who's quietly buying the dip."
        )
    raise KeyError(category)  # pragma: no cover -- guarded by the caller


def market_prompt(category: str, reporter: Reporter, events: dict) -> str:
    """Build the user prompt for a market story.

    ``category`` is one of PROFESSIONAL_DIGEST / GEAR_FOR_YOU / PRIMARY_STATS.
    ``events`` is the dict from ``ahservice.events()`` (carries today's picks and
    each buff's multiplier, plus the mirror-image slump picks/discounts). The
    story covers both the riser (buff) and, when a slump is active, the faller on
    the same axis. Raises KeyError if the category isn't a market one.
    """
    subject_field, mult_field = MARKET_BUFF_FIELD[category]
    subject = events[subject_field]
    movement = _pct(events.get(mult_field, 1.0))

    if category == PROFESSIONAL_DIGEST:
        beat = (
            f"Today's market favors the {subject} trade: goods crafted by "
            f"{subject} are commanding higher prices at auction, {movement} on the "
            "day. Explain the surge, who is buying, and what it means for the "
            f"realm's {subject} artisans and the adventurers who supply their "
            "reagents."
        )
    elif category == GEAR_FOR_YOU:
        beat = (
            f"Today the markets are hot for {subject}. Demand has lifted their "
            f"auction prices {movement}. Tell adventurers what's moving, who should "
            "be shopping (or selling) this category right now, and what a smart "
            "buyer does about it before the day turns."
        )
    elif category == PRIMARY_STATS:
        beat = (
            f"Today the prized attribute on every adventurer's lips is {subject}. "
            f"Gear and goods granting {subject} are {movement} at auction. Explain "
            f"who covets {subject} and why the demand for it has spiked today."
        )
    else:  # pragma: no cover -- guarded by MARKET_BUFF_FIELD lookup above
        raise KeyError(category)

    # Weave in the slump (the day's loser on this axis) when one is active.
    slump_field, slump_mult_field = MARKET_SLUMP_FIELD[category]
    if events.get("discount_enabled") and events.get(slump_field):
        beat += " " + _slump_beat(
            category, events[slump_field],
            _pct(events.get(slump_mult_field, 1.0)))

    return "\n\n".join([
        SETTING_PRIMER,
        MARKET_NPCS,  # auctioneers belong to market stories only
        _reporter_block(reporter),
        f"TODAY'S STORY ({CATEGORY_TITLES[category]}):\n{beat}",
        # A market/economic update is brief by nature.
        _output_contract("short"),
    ])


# --- heroic exploits ---------------------------------------------------------
# A character record fed to the exploit prompts. The builders tolerate missing
# optional keys -- exploits.py fills the deed buckets from the chronicle. Shape::
#
#     {
#       "name": "Aldrik",                    # required
#       "race": "Human",                    # optional
#       "class": "Paladin",                 # optional
#       "level": 80,                        # optional, NEVER rendered (read as
#                                           #   age by reporters); internal only
#       "guild": "The Wakeful",             # optional
#       "professions": ["Blacksmithing",    # optional, professions TRAINED
#                       "Mining"],
#       "leveled": "max" | "up" | None,      # optional, a leveling milestone flag
#                                           #   (no number -- 'max' = hit the cap)
#       "kills": [{"name": "Onyxia",         # optional, notable foes felled
#                  "kind": "worldboss"}],    #   kind: worldboss|boss|rare
#       "prof_milestones": [{"skill": "Alchemy",   # optional, rank ups
#                            "rank": "Grand Master"}],
#       "achievements": [                    # optional, recently earned
#         {"name": "The Argent Champion", "date": "today"},
#       ],
#       "reputations": [{"name": "Argent Crusade"}],  # optional, exalted with
#       "quests": [{"name": "...", "giver": "...",   # optional, solo quests as
#                   "ender": "...", "location": "...",  #   adventure records:
#                   "objective": "...", "objectives": [...],  # see _quest_block
#                   "description": "...",            #   for how each is rendered
#                   "chain": [{"name": "..."}],      #   full chain, if any
#                   "witnesses": ["..."]}],
#       "loot": [{"name": "Quel'Serrar"}],   # optional, notable loot
#       "arsenal": {"weapons": [{"name","type","quality"}],  # boss/elite slayers
#                   "weapon_skills": [{"name","value","max"}],
#                   "spells": ["Frostbolt", ...]},
#       "scene": {"location": "Aerie Peak",  # where the anchor deed happened +
#                 "witnesses": ["..."]},     #   nearby NPCs (up to 3)
#     }
#
# Obituary subjects additionally carry::
#     "death": {"detail": "Slain by Onyxia", "level": 80, "when": "today",
#               "location": "Aerie Peak", "witnesses": ["..."]}
def _character_descriptor(c: dict) -> str:
    """A natural-language phrase identifying a character: name, race/class,
    professions, guild -- whatever fields are present. Level is deliberately
    omitted (reporters kept misreading it as the character's age)."""
    bits = [c.get("name", "an unnamed adventurer")]
    rc = " ".join(x for x in (c.get("race", ""), c.get("class", "")) if x)
    if rc.strip():
        bits.append(f"a {rc.strip()}")
    profs = [p for p in c.get("professions", []) if p]
    if profs:
        bits.append(f"skilled in {' and '.join(profs)}")
    if c.get("guild"):
        bits.append(f"of the guild {c['guild']}")
    return ", ".join(bits)


_KILL_VERB = {
    "worldboss": "Vanquished the world-boss",
    "boss": "Brought down",
    "rare": "Hunted down",
}


def _trim(text: str | None, limit: int) -> str:
    """Collapse whitespace and cap length so a quest's lore/objective text can't
    blow up the prompt."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _witness_line(witnesses: list, label: str = "Nearby, and likely "
                  "witnesses") -> str:
    """A bullet naming up to a few bystanders, or '' when there are none."""
    names = [w for w in (witnesses or []) if w]
    if not names:
        return ""
    return f"  {label}: {', '.join(names)}."


def _quest_block(adv: dict) -> str:
    """Render one quest as an adventure: who gave it, where, what it asked, its
    lore, and -- if it belongs to a chain -- the whole chain to tell as one
    continuing tale. Tolerates any field being absent."""
    title = adv.get("name") or "an unnamed errand"
    weight = f" [{adv['importance']}]" if adv.get("importance") else ""
    head = f"- Quest: \"{title}\"{weight}"
    giver, ender, loc = adv.get("giver"), adv.get("ender"), adv.get("location")
    if giver:
        head += f", given by {giver}" + (f" at {loc}" if loc else "")
    elif loc:
        head += f", taken up at {loc}"
    if ender:
        head += f", and reported back to {ender}"
    head += "."
    lines = [head]
    objective = adv.get("objective") or "; ".join(adv.get("objectives", []))
    if objective:
        lines.append(f"  The charge: {_trim(objective, 240)}")
    elif adv.get("objectives"):
        lines.append(f"  The charge: {_trim('; '.join(adv['objectives']), 240)}")
    if adv.get("description"):
        lines.append(f"  Lore: {_trim(adv['description'], 320)}")
    chain = adv.get("chain") or []
    if chain:
        names = " -> ".join(f"\"{c['name']}\"" for c in chain if c.get("name"))
        lines.append("  This is one link in a longer quest chain -- tell it as a "
                     f"single continuing adventure across all of: {names}.")
    wl = _witness_line(adv.get("witnesses"))
    if wl:
        lines.append(wl)
    return "\n".join(lines)


def _deeds_block(c: dict) -> str:
    """The bulleted list of one character's recent deeds, drawn from the typed
    chronicle buckets. Tolerates any bucket being absent or empty."""
    deeds: list[str] = []
    # Leveling is a milestone flag, never a number (see _character_descriptor).
    if c.get("leveled") == "max":
        deeds.append("- Reached the maximum level of power -- the pinnacle of an "
                     "adventuring career.")
    elif c.get("leveled") == "up":
        deeds.append("- Climbed to a new level of power.")
    for k in c.get("kills", []):
        verb = _KILL_VERB.get(k.get("kind"), "Defeated")
        deeds.append(f"- {verb} {k['name']}.")
    for p in c.get("prof_milestones", []):
        deeds.append(f"- Rose to {p['rank']} in {p['skill']}.")
    for a in c.get("achievements", []):
        when = f" ({a['date']})" if a.get("date") else ""
        deeds.append(f"- Earned the achievement \"{a['name']}\"{when}.")
    for r in c.get("reputations", []):
        deeds.append(f"- Won exalted standing with {r['name']}.")
    for q in c.get("quests", []):
        deeds.append(_quest_block(q))
    for it in c.get("loot", []):
        deeds.append(f"- Claimed the prize {it['name']}.")
    return "\n".join(deeds) if deeds else "- (No notable deeds recorded.)"


def _arsenal_block(c: dict) -> str:
    """For a boss/elite slayer: the weapons they wield, the weapon families they
    are most practiced with, and their most advanced spells -- so the telling
    can show which arms and arts they favour. '' when no arsenal was gathered."""
    a = c.get("arsenal") or {}
    weps, skills, spells = (a.get("weapons", []), a.get("weapon_skills", []),
                            a.get("spells", []))
    if not (weps or skills or spells):
        return ""
    lines = ["How they fight (weave this in; favour what they are most skilled "
             "with):"]
    if weps:
        lines.append("- Wielding: "
                     + ", ".join(f"{w['name']} (a {w['type']})" for w in weps)
                     + ".")
    if skills:
        lines.append("- Most practiced weapon skills: "
                     + ", ".join(f"{s['name']} ({s['value']}/{s['max']})"
                                 for s in skills) + ".")
    if spells:
        lines.append("- Signature spells and abilities: "
                     + ", ".join(spells) + ".")
    return "\n".join(lines)


def _scene_block(c: dict) -> str:
    """Where the deed happened and who was about to see it, from the article's
    anchor event. '' when no scene was resolved."""
    scene = c.get("scene") or {}
    loc, wit = scene.get("location"), scene.get("witnesses")
    bits = []
    if loc:
        bits.append(f"The deed unfolded near {loc}.")
    names = [w for w in (wit or []) if w]
    if names:
        bits.append(f"Bystanders who would have witnessed it: {', '.join(names)}.")
    return " ".join(bits)


def exploits_prompt(reporter: Reporter, exploits: dict) -> str:
    """Build the user prompt for a Heroic Exploits story about ONE character."""
    extras = "\n\n".join(b for b in (_scene_block(exploits),
                                     _arsenal_block(exploits)) if b)
    beat = (
        f"The subject of today's story is {_character_descriptor(exploits)}. Here "
        "is what they have lately accomplished (a bracketed weight marks how much "
        "each quest mattered):\n"
        f"{_deeds_block(exploits)}"
        + (f"\n\n{extras}" if extras else "")
        + "\n\nWrite an article celebrating these exploits. Stay faithful to the "
        "facts above -- name the actual foes, quests, places and bystanders -- but "
        "tell it as a story worth reading. Give the weighty deeds room; a trivial "
        "errand earns only a passing line, or none. Where a quest belongs to a "
        "chain, tell it as one continuing adventure. Where it fits, let their class "
        "and trade colour the telling, and let named bystanders react. Tie the "
        "deeds to the towns and folk of the realm who would have heard the news."
    )
    return "\n\n".join([
        SETTING_PRIMER,
        _reporter_block(reporter),
        f"TODAY'S STORY ({CATEGORY_TITLES[HEROIC_EXPLOITS]}):\n{beat}",
        _output_contract(exploits.get("scale", "standard")),
    ])


def group_exploits_prompt(reporter: Reporter, group: dict) -> str:
    """Build the user prompt for a Heroic Exploits story covering SEVERAL
    characters who shared the same feat the same day -- either completing the
    same quest or felling the same boss.

    ``group`` shape::

        {
          "feat": {"kind": "quest" | "boss",          # required
                   "name": "Onyxia"},                 # required
          "characters": [ <character dict>, ... ],     # 2+ subjects
        }
    """
    feat = group.get("feat", {})
    name = feat.get("name", "a notable feat")
    detail = ""
    if feat.get("kind") == "boss":
        shared = (f"Several adventurers brought down the same fearsome foe today: "
                  f"\"{name}\".")
        constraint = "Do not invent deeds beyond felling this foe together."
        loc, wit = feat.get("location"), feat.get("witnesses")
        scene_bits = []
        if loc:
            scene_bits.append(f"The foe was felled near {loc}.")
        names = [w for w in (wit or []) if w]
        if names:
            scene_bits.append("Bystanders who would have witnessed it: "
                              f"{', '.join(names)}.")
        detail = " ".join(scene_bits)
    else:
        shared = (f"Several adventurers completed the same quest today: \"{name}\".")
        constraint = "Do not invent deeds beyond completing this quest together."
        adv = feat.get("adventure")
        if adv:
            # Reuse the solo quest-adventure rendering for the shared quest.
            detail = "The adventure they shared:\n" + _quest_block(adv)

    roster = "\n".join(f"- {_character_descriptor(c)}"
                       for c in group.get("characters", []))
    beat = (
        f"{shared} Those who shared in it:\n"
        f"{roster}"
        + (f"\n\n{detail}" if detail else "")
        + "\n\nWrite ONE story about this shared feat -- a band of adventurers "
        "converging on the same deed the same day. Name the feat, the place and "
        "the characters, and let their classes and trades distinguish them in the "
        f"telling. Where the quest belongs to a chain, tell it as one continuing "
        f"adventure. {constraint} Tie it to the towns and folk of the realm who "
        "would have heard the news."
    )
    return "\n\n".join([
        SETTING_PRIMER,
        _reporter_block(reporter),
        f"TODAY'S STORY ({CATEGORY_TITLES[HEROIC_EXPLOITS]}):\n{beat}",
        _output_contract(group.get("scale", "standard")),
    ])


# --- obituaries --------------------------------------------------------------
def obituary_prompt(reporter: Reporter, subject: dict) -> str:
    """Build the user prompt for an Obituary commemorating ONE fallen character.

    ``subject`` is a character dict (see the shape note above) carrying a
    ``death`` block: ``{"detail": "Slain by Onyxia", "level": 80, "when":
    "today"}``. ``detail`` may be empty for a death with no slayer (a fall, the
    elements), which the obituary should render as the wilds claiming them.
    """
    death = subject.get("death", {})
    detail = (death.get("detail") or "").strip()
    if detail.lower().startswith("slain by"):
        cause = f"They fell {detail[0].lower() + detail[1:]}."
    elif detail:
        cause = f"They fell: {detail}."
    else:
        cause = "They fell to the perils of the wild -- no foe's hand, but the "\
                "land itself or a fatal misstep."
    when = f" ({death['when']})" if death.get("when") else ""
    where = f" They fell near {death['location']}." if death.get("location") else ""
    names = [w for w in (death.get("witnesses") or []) if w]
    witnessed = (f" Nearby when they fell: {', '.join(names)}." if names else "")

    beat = (
        f"A fallen adventurer is to be commemorated: {_character_descriptor(subject)}."
        f"\n{cause}{when}{where}{witnessed}\n\n"
        "Write a VERY SHORT elegiac POEM in your voice -- a brief verse of a few "
        "lines, one thought per line. It must be verse, never prose. Name who they "
        "were, where and how they met their end, faithfully; do not invent deeds or "
        "survivors you were not told of. Respectful and in-world. Ignore the "
        "general length guidance above -- an obituary is far shorter than an "
        "article."
    )
    return "\n\n".join([
        SETTING_PRIMER,
        _reporter_block(reporter),
        f"TODAY'S NOTICE ({CATEGORY_TITLES[OBITUARIES]}):\n{beat}",
        _obituary_contract(death.get("level")),
    ])


# --- reporter selection ------------------------------------------------------
def pick_reporter(category: str, seed=None) -> Reporter:
    """Choose a reporter for a category. Pass a ``seed`` (e.g. the date, or a
    character guid) for a stable, reproducible pick; omit it for a random one."""
    roster = ROSTERS[category]
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(roster)


def reporter_by_key(category: str, key: str) -> Reporter | None:
    """Look up a specific reporter in a category's roster, or None."""
    return next((r for r in ROSTERS[category] if r.key == key), None)
