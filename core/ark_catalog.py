"""ARK: Survival Ascended / Evolved names for the cluster tools: creatures (for spawn
rules), items (for loot tables) and loot containers (supply drops, caves, boss and
alpha drops). Class names come from ark.wiki.gg "Creature IDs" and arkcodes.com;
anything missing (mods, new DLC) can be typed in as a class name in the panels.
"""

from __future__ import annotations

from typing import Dict, List

# (display name, class name, group)
CREATURES: List[tuple] = [
    # land - herbivores
    ("Ankylosaurus", "Ankylo_Character_BP_C", "Land"), ("Brontosaurus", "Sauropod_Character_BP_C", "Land"),
    ("Carbonemys", "Turtle_Character_BP_C", "Land"), ("Castoroides", "Beaver_Character_BP_C", "Land"),
    ("Chalicotherium", "Chalico_Character_BP_C", "Land"), ("Diplodocus", "Diplodocus_Character_BP_C", "Land"),
    ("Dodo", "Dodo_Character_BP_C", "Land"), ("Doedicurus", "Doed_Character_BP_C", "Land"),
    ("Equus", "Equus_Character_BP_C", "Land"), ("Gallimimus", "Galli_Character_BP_C", "Land"),
    ("Iguanodon", "Iguanodon_Character_BP_C", "Land"), ("Kentrosaurus", "Kentro_Character_BP_C", "Land"),
    ("Lystrosaurus", "Lystro_Character_BP_C", "Land"), ("Mammoth", "Mammoth_Character_BP_C", "Land"),
    ("Megaloceros", "Stag_Character_BP_C", "Land"), ("Megatherium", "Megatherium_Character_BP_C", "Land"),
    ("Moschops", "Moschops_Character_BP_C", "Land"), ("Ovis", "Sheep_Character_BP_C", "Land"),
    ("Pachy", "Pachy_Character_BP_C", "Land"), ("Pachyrhinosaurus", "Pachyrhino_Character_BP_C", "Land"),
    ("Paraceratherium", "Paracer_Character_BP_C", "Land"), ("Parasaur", "Para_Character_BP_C", "Land"),
    ("Phiomia", "Phiomia_Character_BP_C", "Land"), ("Stegosaurus", "Stego_Character_BP_C", "Land"),
    ("Triceratops", "Trike_Character_BP_C", "Land"), ("Woolly Rhino", "Rhino_Character_BP_C", "Land"),
    ("Therizinosaurus", "Therizino_Character_BP_C", "Land"), ("Bison", "Bison_Character_BP_C", "Land"),
    ("Dreadnoughtus", "Dreadnoughtus_Character_BP_C", "Land"),
    # land - predators
    ("Acrocanthosaurus", "Acrocanthosaurus_Character_BP_C", "Predators"),
    ("Allosaurus", "Allo_Character_BP_C", "Predators"), ("Baryonyx", "Baryonyx_Character_BP_C", "Predators"),
    ("Carcharodontosaurus", "Carcha_Character_BP_C", "Predators"),
    ("Carnotaurus", "Carno_Character_BP_C", "Predators"), ("Ceratosaurus", "Ceratosaurus_Character_BP_ASA_C", "Predators"),
    ("Daeodon", "Daeodon_Character_BP_C", "Predators"), ("Dilophosaur", "Dilo_Character_BP_C", "Predators"),
    ("Dimetrodon", "Dimetro_Character_BP_C", "Predators"), ("Dire Bear", "Direbear_Character_BP_C", "Predators"),
    ("Direwolf", "Direwolf_Character_BP_C", "Predators"), ("Giganotosaurus", "Gigant_Character_BP_C", "Predators"),
    ("Gigantopithecus", "Bigfoot_Character_BP_C", "Predators"), ("Gigantoraptor", "Gigantoraptor_Character_BP_C", "Predators"),
    ("Hyaenodon", "Hyaenodon_Character_BP_C", "Predators"), ("Kaprosuchus", "Kaprosuchus_Character_BP_C", "Predators"),
    ("Megalania", "Megalania_Character_BP_C", "Predators"), ("Megalosaurus", "Megalosaurus_Character_BP_C", "Predators"),
    ("Purlovia", "Purlovia_Character_BP_C", "Predators"), ("Raptor", "Raptor_Character_BP_C", "Predators"),
    ("Rex", "Rex_Character_BP_C", "Predators"), ("Sabertooth", "Saber_Character_BP_C", "Predators"),
    ("Sarco", "Sarco_Character_BP_C", "Predators"), ("Spinosaurus", "Spino_Character_BP_C", "Predators"),
    ("Terror Bird", "TerrorBird_Character_BP_C", "Predators"), ("Thylacoleo", "Thylacoleo_Character_BP_C", "Predators"),
    ("Titanoboa", "BoaFrill_Character_BP_C", "Predators"), ("Troodon", "Troodon_Character_BP_C", "Predators"),
    ("Yutyrannus", "Yutyrannus_Character_BP_C", "Predators"), ("Deinosuchus", "Deinosuchusasa_Character_BP_C", "Predators"),
    ("Compy", "Compy_Character_BP_C", "Predators"), ("Procoptodon", "Procoptodon_Character_BP_C", "Land"),
    ("Deinotherium", "DeinotheriumASA_Character_BP_C", "Land"),
    # flyers
    ("Archaeopteryx", "Archa_Character_BP_C", "Flyers"), ("Argentavis", "Argent_Character_BP_C", "Flyers"),
    ("Dimorphodon", "Dimorph_Character_BP_C", "Flyers"), ("Ichthyornis", "Ichthyornis_Character_BP_C", "Flyers"),
    ("Microraptor", "Microraptor_Character_BP_C", "Flyers"), ("Onyc (bats)", "Bat_Character_BP_C", "Flyers"),
    ("Pelagornis", "Pela_Character_BP_C", "Flyers"), ("Pteranodon", "Ptero_Character_BP_C", "Flyers"),
    ("Quetzal", "Quetz_Character_BP_C", "Flyers"), ("Tapejara", "Tapejara_Character_BP_C", "Flyers"),
    ("Hesperornis", "Hesperornis_Character_BP_C", "Flyers"),
    # water
    ("Ammonite", "Ammonite_Character_C", "Water"), ("Basilosaurus", "Basilosaurus_Character_BP_C", "Water"),
    ("Cnidaria (jellyfish)", "Cnidaria_Character_BP_C", "Water"), ("Coelacanth", "Coel_Character_BP_C", "Water"),
    ("Dunkleosteus", "Dunkle_Character_BP_C", "Water"), ("Electrophorus (eel)", "Eel_Character_BP_C", "Water"),
    ("Eurypterid", "Euryp_Character_C", "Water"), ("Ichthyosaurus", "Dolphin_Character_BP_C", "Water"),
    ("Leedsichthys", "Leedsichthys_Character_BP_C", "Water"), ("Liopleurodon", "Liopleurodon_Character_BP_C", "Water"),
    ("Manta", "Manta_Character_BP_C", "Water"), ("Megalodon", "Megalodon_Character_BP_C", "Water"),
    ("Mosasaurus", "Mosa_Character_BP_C", "Water"), ("Piranha", "Piranha_Character_BP_C", "Water"),
    ("Plesiosaur", "Plesiosaur_Character_BP_C", "Water"), ("Sabertooth Salmon", "Salmon_Character_BP_C", "Water"),
    ("Trilobite", "Trilobite_Character_C", "Water"), ("Tusoteuthis", "Tusoteuthis_Character_BP_C", "Water"),
    ("Anglerfish", "Angler_Character_BP_C", "Water"), ("Helicoprion", "Helicoprion_Character_BP_C", "Water"),
    ("Otter", "Otter_Character_BP_C", "Water"), ("Beelzebufo (frog)", "Toad_Character_BP_C", "Water"),
    ("Diplocaulus", "Diplocaulus_Character_BP_C", "Water"), ("Kairuku", "Kairuku_Character_BP_C", "Water"),
    ("Archelon", "Archelon_Character_BP_ASA_C", "Water"),
    # bugs & pests
    ("Achatina", "Achatina_Character_BP_C", "Bugs & pests"), ("Araneo (spider)", "SpiderS_Character_BP_C", "Bugs & pests"),
    ("Arthropluera", "Arthro_Character_BP_C", "Bugs & pests"), ("Dung Beetle", "DungBeetle_Character_BP_C", "Bugs & pests"),
    ("Giant Bee", "Bee_Character_BP_C", "Bugs & pests"), ("Leech", "Leech_Character_C", "Bugs & pests"),
    ("Diseased Leech", "Leech_Character_Diseased_C", "Bugs & pests"), ("Meganeura", "Dragonfly_Character_BP_C", "Bugs & pests"),
    ("Mesopithecus", "Monkey_Character_BP_C", "Bugs & pests"), ("Oviraptor", "Oviraptor_Character_BP_C", "Bugs & pests"),
    ("Pegomastax", "Pegomastax_Character_BP_C", "Bugs & pests"), ("Pulmonoscorpius", "Scorpion_Character_BP_C", "Bugs & pests"),
    ("Titanomyrma", "Ant_Character_BP_C", "Bugs & pests"), ("Titanomyrma (flying)", "FlyingAnt_Character_BP_C", "Bugs & pests"),
    ("Rhyniognatha", "Rhynio_Character_BP_C", "Bugs & pests"),
    # alphas
    ("Alpha Carnotaurus", "MegaCarno_Character_BP_C", "Alphas"), ("Alpha Raptor", "MegaRaptor_Character_BP_C", "Alphas"),
    ("Alpha Rex", "MegaRex_Character_BP_C", "Alphas"), ("Alpha Megalodon", "MegaMegalodon_Character_BP_C", "Alphas"),
    ("Alpha Mosasaur", "Mosa_Character_BP_Mega_C", "Alphas"), ("Alpha Leedsichthys", "Alpha_Leedsichthys_Character_BP_C", "Alphas"),
    ("Alpha Tusoteuthis", "Mega_Tusoteuthis_Character_BP_C", "Alphas"),
    # Scorched Earth
    ("Jerboa", "Jerboa_Character_BP_C", "Scorched Earth"), ("Lymantria", "Moth_Character_BP_C", "Scorched Earth"),
    ("Morellatops", "camelsaurus_Character_BP_C", "Scorched Earth"), ("Rock Elemental", "RockGolem_Character_BP_C", "Scorched Earth"),
    ("Mantis", "Mantis_Character_BP_C", "Scorched Earth"), ("Vulture", "Vulture_Character_BP_C", "Scorched Earth"),
    ("Thorny Dragon", "SpineyLizard_Character_BP_C", "Scorched Earth"), ("Deathworm", "Deathworm_Character_BP_C", "Scorched Earth"),
    ("Fire Wyvern", "Wyvern_Character_BP_Fire_C", "Scorched Earth"), ("Ash Wyvern", "Wyvern_Character_BP_Ash_C", "Scorched Earth"),
    # Aberration
    ("Basilisk", "Basilisk_Character_BP_C", "Aberration"), ("Ravager", "CaveWolf_Character_BP_C", "Aberration"),
    ("Karkinos", "Crab_Character_BP_C", "Aberration"), ("Bulbdog", "LanternPug_Character_BP_C", "Aberration"),
    ("Featherlight", "LanternBird_Character_BP_C", "Aberration"), ("Glowtail", "LanternLizard_Character_BP_C", "Aberration"),
    ("Shinehorn", "LanternGoat_Character_BP_C", "Aberration"), ("Nameless", "ChupaCabra_Character_BP_C", "Aberration"),
    ("Nameless (surface)", "ChupaCabra_Character_BP_Surface_C", "Aberration"),
]

ITEMS: List[tuple] = [
    # resources
    ("Metal (ore)", "PrimalItemResource_Metal_C", "Resources"), ("Metal Ingot", "PrimalItemResource_MetalIngot_C", "Resources"),
    ("Polymer", "PrimalItemResource_Polymer_C", "Resources"), ("Crystal", "PrimalItemResource_Crystal_C", "Resources"),
    ("Obsidian", "PrimalItemResource_Obsidian_C", "Resources"), ("Cementing Paste", "PrimalItemResource_ChitinPaste_C", "Resources"),
    ("Element", "PrimalItemResource_Element_C", "Resources"), ("Element Shard", "PrimalItemResource_ElementShard_C", "Resources"),
    ("Black Pearl", "PrimalItemResource_BlackPearl_C", "Resources"), ("Silica Pearls", "PrimalItemResource_Silicon_C", "Resources"),
    ("Oil", "PrimalItemResource_Oil_C", "Resources"), ("Hide", "PrimalItemResource_Hide_C", "Resources"),
    ("Pelt", "PrimalItemResource_Pelt_C", "Resources"), ("Chitin", "PrimalItemResource_Chitin_C", "Resources"),
    ("Keratin", "PrimalItemResource_Keratin_C", "Resources"), ("Fiber", "PrimalItemResource_Fibers_C", "Resources"),
    ("Wood", "PrimalItemResource_Wood_C", "Resources"), ("Stone", "PrimalItemResource_Stone_C", "Resources"),
    ("Flint", "PrimalItemResource_Flint_C", "Resources"), ("Thatch", "PrimalItemResource_Thatch_C", "Resources"),
    ("Sparkpowder", "PrimalItemResource_Sparkpowder_C", "Resources"), ("Gunpowder", "PrimalItemResource_Gunpowder_C", "Resources"),
    ("Electronics", "PrimalItemResource_Electronics_C", "Resources"), ("Rare Flower", "PrimalItemResource_RareFlower_C", "Resources"),
    ("Rare Mushroom", "PrimalItemResource_RareMushroom_C", "Resources"), ("Gasoline", "PrimalItemResource_Gasoline_C", "Resources"),
    # weapons
    ("Bow", "PrimalItem_WeaponBow_C", "Weapons"), ("Crossbow", "PrimalItem_WeaponCrossbow_C", "Weapons"),
    ("Compound Bow", "PrimalItem_WeaponCompoundBow_C", "Weapons"), ("Simple Pistol", "PrimalItem_WeaponGun_C", "Weapons"),
    ("Fabricated Pistol", "PrimalItem_WeaponMachinedPistol_C", "Weapons"), ("Longneck Rifle", "PrimalItem_WeaponOneShotRifle_C", "Weapons"),
    ("Fabricated Sniper Rifle", "PrimalItem_WeaponMachinedSniper_C", "Weapons"), ("Assault Rifle", "PrimalItem_WeaponRifle_C", "Weapons"),
    ("Shotgun", "PrimalItem_WeaponShotgun_C", "Weapons"), ("Pump-Action Shotgun", "PrimalItem_WeaponMachinedShotgun_C", "Weapons"),
    ("Rocket Launcher", "PrimalItem_WeaponRocketLauncher_C", "Weapons"), ("Sword", "PrimalItem_WeaponSword_C", "Weapons"),
    ("Pike", "PrimalItem_WeaponPike_C", "Weapons"), ("Metal Pick", "PrimalItem_WeaponMetalPick_C", "Tools"),
    ("Metal Hatchet", "PrimalItem_WeaponMetalHatchet_C", "Tools"), ("Sickle", "PrimalItem_WeaponSickle_C", "Tools"),
    ("Chainsaw", "PrimalItem_ChainSaw_C", "Tools"), ("Spyglass", "PrimalItem_WeaponSpyglass_C", "Tools"),
    ("GPS", "PrimalItem_WeaponGPS_C", "Tools"), ("Magnifying Glass", "PrimalItem_WeaponMagnifyingGlass_C", "Tools"),
    # ammo
    ("Stone Arrow", "PrimalItemAmmo_ArrowStone_C", "Ammo"), ("Tranquilizer Arrow", "PrimalItemAmmo_ArrowTranq_C", "Ammo"),
    ("Metal Arrow", "PrimalItemAmmo_CompoundBowArrow_C", "Ammo"), ("Simple Bullet", "PrimalItemAmmo_SimpleBullet_C", "Ammo"),
    ("Advanced Bullet", "PrimalItemAmmo_AdvancedBullet_C", "Ammo"), ("Advanced Rifle Bullet", "PrimalItemAmmo_AdvancedRifleBullet_C", "Ammo"),
    ("Advanced Sniper Bullet", "PrimalItemAmmo_AdvancedSniperBullet_C", "Ammo"), ("Simple Shotgun Ammo", "PrimalItemAmmo_SimpleShotgunBullet_C", "Ammo"),
    ("Tranquilizer Dart", "PrimalItemAmmo_TranqDart_C", "Ammo"), ("Rocket Propelled Grenade", "PrimalItemAmmo_Rocket_C", "Ammo"),
    # armor
    ("Flak Helmet", "PrimalItemArmor_MetalHelmet_C", "Armor"), ("Flak Chestpiece", "PrimalItemArmor_MetalShirt_C", "Armor"),
    ("Flak Leggings", "PrimalItemArmor_MetalPants_C", "Armor"), ("Flak Gauntlets", "PrimalItemArmor_MetalGloves_C", "Armor"),
    ("Flak Boots", "PrimalItemArmor_MetalBoots_C", "Armor"), ("Riot Helmet", "PrimalItemArmor_RiotHelmet_C", "Armor"),
    ("Riot Chestpiece", "PrimalItemArmor_RiotShirt_C", "Armor"), ("Riot Leggings", "PrimalItemArmor_RiotPants_C", "Armor"),
    ("Riot Gauntlets", "PrimalItemArmor_RiotGloves_C", "Armor"), ("Riot Boots", "PrimalItemArmor_RiotBoots_C", "Armor"),
    ("Ghillie Chestpiece", "PrimalItemArmor_GhillieShirt_C", "Armor"), ("Fur Chestpiece", "PrimalItemArmor_FurShirt_C", "Armor"),
    ("Hazard Suit Shirt", "PrimalItemArmor_HazardSuitShirt_C", "Armor"), ("Wooden Shield", "PrimalItemArmor_WoodShield_C", "Armor"),
    ("Metal Shield", "PrimalItemArmor_MetalShield_C", "Armor"), ("Riot Shield", "PrimalItemArmor_TransparentRiotShield_C", "Armor"),
    # saddles
    ("Rex Saddle", "PrimalItemArmor_RexSaddle_C", "Saddles"), ("Argentavis Saddle", "PrimalItemArmor_ArgentavisSaddle_C", "Saddles"),
    ("Pteranodon Saddle", "PrimalItemArmor_PteroSaddle_C", "Saddles"), ("Raptor Saddle", "PrimalItemArmor_RaptorSaddle_C", "Saddles"),
    ("Trike Saddle", "PrimalItemArmor_TrikeSaddle_C", "Saddles"), ("Stego Saddle", "PrimalItemArmor_StegoSaddle_C", "Saddles"),
    ("Spino Saddle", "PrimalItemArmor_SpinoSaddle_C", "Saddles"), ("Ankylo Saddle", "PrimalItemArmor_AnkyloSaddle_C", "Saddles"),
    ("Doedicurus Saddle", "PrimalItemArmor_DoedSaddle_C", "Saddles"), ("Mammoth Saddle", "PrimalItemArmor_MammothSaddle_C", "Saddles"),
    ("Quetzal Saddle", "PrimalItemArmor_QuetzSaddle_C", "Saddles"), ("Megalodon Saddle", "PrimalItemArmor_MegalodonSaddle_C", "Saddles"),
    ("Mosasaur Saddle", "PrimalItemArmor_MosaSaddle_C", "Saddles"), ("Sarco Saddle", "PrimalItemArmor_SarcoSaddle_C", "Saddles"),
    ("Therizinosaur Saddle", "PrimalItemArmor_TherizinosaurusSaddle_C", "Saddles"),
    # consumables
    ("Medical Brew", "PrimalItemConsumable_HealSoup_C", "Consumables"), ("Stimulant", "PrimalItemConsumable_Stimulant_C", "Consumables"),
    ("Narcotic", "PrimalItemConsumable_Narcotic_C", "Consumables"), ("Cooked Meat", "PrimalItemConsumable_CookedMeat_C", "Consumables"),
    ("Cooked Prime Meat", "PrimalItemConsumable_CookedPrimeMeat_C", "Consumables"),
    ("Mindwipe Tonic", "PrimalItemConsumable_TheHorn_C", "Consumables"),
    # structures / misc
    ("Fabricator", "PrimalItemStructure_Fabricator_C", "Structures"), ("Industrial Forge", "PrimalItemStructure_IndustrialForge_C", "Structures"),
    ("Refining Forge", "PrimalItemStructure_Forge_C", "Structures"), ("Air Conditioner", "PrimalItemStructure_AirConditioner_C", "Structures"),
    ("Generator", "PrimalItemStructure_PowerGenerator_C", "Structures"), ("Water Tank", "PrimalItemStructure_WaterTank_C", "Structures"),
]

# loot containers: (display name, class, group, kind) kind "crate" | "dino"
CRATES: List[tuple] = [
    ("White drop (level 3)", "SupplyCrate_Level03_C", "Supply drops", "crate"),
    ("White drop - double", "SupplyCrate_Level03_Double_C", "Supply drops", "crate"),
    ("Green drop (level 15)", "SupplyCrate_Level15_C", "Supply drops", "crate"),
    ("Green drop - double", "SupplyCrate_Level15_Double_C", "Supply drops", "crate"),
    ("Blue drop (level 25)", "SupplyCrate_Level25_C", "Supply drops", "crate"),
    ("Blue drop - double", "SupplyCrate_Level25_Double_C", "Supply drops", "crate"),
    ("Purple drop (level 35)", "SupplyCrate_Level35_C", "Supply drops", "crate"),
    ("Purple drop - double", "SupplyCrate_Level35_Double_C", "Supply drops", "crate"),
    ("Yellow drop (level 45)", "SupplyCrate_Level45_C", "Supply drops", "crate"),
    ("Yellow drop - double", "SupplyCrate_Level45_Double_C", "Supply drops", "crate"),
    ("Red drop (level 60)", "SupplyCrate_Level60_C", "Supply drops", "crate"),
    ("Red drop - double", "SupplyCrate_Level60_Double_C", "Supply drops", "crate"),
    ("Cave crate - tier 1", "SupplyCrate_Cave_QualityTier1_C", "Caves & ocean", "crate"),
    ("Cave crate - tier 2", "SupplyCrate_Cave_QualityTier2_C", "Caves & ocean", "crate"),
    ("Cave crate - tier 3", "SupplyCrate_Cave_QualityTier3_C", "Caves & ocean", "crate"),
    ("Cave crate - tier 4", "SupplyCrate_Cave_QualityTier4_C", "Caves & ocean", "crate"),
    ("Ice cave crate - tier 1", "SupplyCrate_IceCaveTier1_C", "Caves & ocean", "crate"),
    ("Ice cave crate - tier 2", "SupplyCrate_IceCaveTier2_C", "Caves & ocean", "crate"),
    ("Ice cave crate - tier 3", "SupplyCrate_IceCaveTier3_C", "Caves & ocean", "crate"),
    ("Swamp cave crate", "SupplyCrate_SwampCaveTier1_C", "Caves & ocean", "crate"),
    ("Deep sea crate", "SupplyCrate_OceanInstant_C", "Caves & ocean", "crate"),
    ("Boss reward", "SupplyCrate_BossLoot_C", "Bosses", "crate"),
    ("Boss reward - Gamma", "SupplyCrate_BossLoot_Gamma_C", "Bosses", "crate"),
    ("Boss reward - Beta", "SupplyCrate_BossLoot_Beta_C", "Bosses", "crate"),
    ("Boss reward - Alpha", "SupplyCrate_BossLoot_Alpha_C", "Bosses", "crate"),
    ("Broodmother drop - Gamma", "DinoDropInventoryComponent_BossSpider_Easy_C", "Bosses", "dino"),
    ("Broodmother drop - Beta", "DinoDropInventoryComponent_BossSpider_Medium_C", "Bosses", "dino"),
    ("Broodmother drop - Alpha", "DinoDropInventoryComponent_BossSpider_Hard_C", "Bosses", "dino"),
    ("Megapithecus drop - Gamma", "DinoDropInventoryComponent_BossGorilla_Easy_C", "Bosses", "dino"),
    ("Megapithecus drop - Beta", "DinoDropInventoryComponent_BossGorilla_Medium_C", "Bosses", "dino"),
    ("Megapithecus drop - Alpha", "DinoDropInventoryComponent_BossGorilla_Hard_C", "Bosses", "dino"),
    ("Dragon drop - Gamma", "DinoDropInventoryComponent_BossDragon_Easy_C", "Bosses", "dino"),
    ("Dragon drop - Beta", "DinoDropInventoryComponent_BossDragon_Medium_C", "Bosses", "dino"),
    ("Dragon drop - Alpha", "DinoDropInventoryComponent_BossDragon_Hard_C", "Bosses", "dino"),
    ("Manticore drop - Gamma", "DinoDropInventoryComponent_BossManticore_Easy_C", "Bosses", "dino"),
    ("Manticore drop - Beta", "DinoDropInventoryComponent_BossManticore_Medium_C", "Bosses", "dino"),
    ("Manticore drop - Alpha", "DinoDropInventoryComponent_BossManticore_Hard_C", "Bosses", "dino"),
    ("Acrocanthosaurus drop", "DinoDropInventoryComponent_Carnivore_Huge_Acro_C", "Creature drops", "dino"),
    ("Deinosuchus drop", "DinoDropInventoryComponent_Carnivore_Huge_Deino_ASA_C", "Creature drops", "dino"),
    ("Basilosaurus drop", "DinoDropInventoryComponent_Basilo_C", "Creature drops", "dino"),
    ("Scorched white (level 3)", "SupplyCrate_Level03_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched green (level 15)", "SupplyCrate_Level15_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched blue (level 30)", "SupplyCrate_Level30_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched purple (level 45)", "SupplyCrate_Level45_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched yellow (level 55)", "SupplyCrate_Level55_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched red (level 70)", "SupplyCrate_Level70_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched cave - tier 1", "SupplyCrate_Cave_QualityTier1_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched cave - tier 2", "SupplyCrate_Cave_QualityTier2_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Scorched cave - tier 3", "SupplyCrate_Cave_QualityTier3_ScorchedEarth_C", "Scorched Earth", "crate"),
    ("Aberration surface (level 35)", "SupplyCrate_Level35_Aberrant_Surface_C", "Aberration", "crate"),
    ("Aberration surface (level 50)", "SupplyCrate_Level50_Aberrant_Surface_C", "Aberration", "crate"),
    ("Aberration surface (level 65)", "SupplyCrate_Level65_Aberrant_Surface_C", "Aberration", "crate"),
    ("Aberration surface (level 80)", "SupplyCrate_Level80_Aberrant_Surface_C", "Aberration", "crate"),
    ("Aberration cave (level 10)", "SupplyCrate_Cave_Aberration_Level10_C", "Aberration", "crate"),
    ("Aberration cave (level 25)", "SupplyCrate_Cave_Aberration_Level25_C", "Aberration", "crate"),
    ("Aberration cave (level 35)", "SupplyCrate_Cave_Aberration_Level35_C", "Aberration", "crate"),
    ("Aberration cave (level 50)", "SupplyCrate_Cave_Aberration_Level50_C", "Aberration", "crate"),
    ("Aberration cave (level 65)", "SupplyCrate_Cave_Aberration_Level65_C", "Aberration", "crate"),
    ("Aberration cave (level 80)", "SupplyCrate_Cave_Aberration_Level80_C", "Aberration", "crate"),
    ("Astraeos blue (level 25)", "SupplyCrate_Level25_Astraeos_C", "Astraeos", "crate"),
    ("Astraeos purple (level 35)", "SupplyCrate_Level35_Astraeos_C", "Astraeos", "crate"),
    ("Astraeos yellow (level 45)", "SupplyCrate_Level45_Astraeos_C", "Astraeos", "crate"),
    ("Astraeos red (level 60)", "SupplyCrate_Level60_Astraeos_C", "Astraeos", "crate"),
]

# item quality: ARK's own numbers behind the tier names (approximate)
QUALITY_TIERS = [("Primitive", 0.0), ("Ramshackle", 1.25), ("Apprentice", 2.5), ("Journeyman", 4.5),
                 ("Mastercraft", 7.0), ("Ascendant", 10.0)]


def _index(rows) -> Dict[str, str]:
    return {r[1].lower(): r[0] for r in rows}


_CRE, _ITEM, _CRATE = _index(CREATURES), _index(ITEMS), _index(CRATES)


def creature_name(cls: str) -> str:
    return _CRE.get((cls or "").lower()) or pretty(cls)


def item_name(cls: str) -> str:
    return _ITEM.get((cls or "").lower()) or pretty(cls)


def crate_name(cls: str) -> str:
    return _CRATE.get((cls or "").lower()) or pretty(cls)


def pretty(cls: str) -> str:
    """Best-effort readable name for a class we don't know (mods)."""
    import re
    s = re.sub(r"^(PrimalItem(Resource|Armor|Consumable|Ammo|Structure)?_|PrimalItem_Weapon)", "", cls or "")
    s = re.sub(r"(_Character)?(_BP)?(_ASA)?_C$", "", s)
    s = s.replace("_", " ").strip()
    return s or (cls or "")


def as_json() -> dict:
    return {"creatures": [{"name": n, "cls": c, "group": g} for n, c, g in CREATURES],
            "items": [{"name": n, "cls": c, "group": g} for n, c, g in ITEMS],
            "crates": [{"name": n, "cls": c, "group": g, "kind": k} for n, c, g, k in CRATES],
            "quality": [{"name": n, "value": v} for n, v in QUALITY_TIERS]}
