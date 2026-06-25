"""Finance — calculs de domaine français standards (non-data, non-connecteurs).

Comme le module `geo`, ce sont des **utilitaires purs réutilisables**, pas des
connecteurs : la donnée d'entrée est fournie par l'appelant. La capacité d'emprunt
HCSF est une **règle nationale** (Haut Conseil de Stabilité Financière), pas une
spécificité métier d'un produit.
"""
from __future__ import annotations


def capacite_emprunt_hcsf(
    revenu_net_mensuel: float,
    taux_annuel: float = 0.038,
    duree_ans: int = 25,
    taux_endettement: float = 0.35,
) -> int:
    """Capacité d'emprunt maximale selon la norme HCSF.

    Annuité plafonnée à `taux_endettement` × revenu (HCSF : 35 %), sur `duree_ans`
    (max HCSF 25 ans) au `taux_annuel` nominal. Renvoie le capital empruntable,
    arrondi au millier d'euros.

    Args:
        revenu_net_mensuel: revenu net mensuel du ménage (€).
        taux_annuel: taux nominal annuel (ex. 0.038 = 3,8 %).
        duree_ans: durée du prêt en années.
        taux_endettement: part max du revenu en mensualité (HCSF = 0.35).
    """
    mensualite_max = revenu_net_mensuel * taux_endettement
    nb_mois = duree_ans * 12
    taux_mensuel = taux_annuel / 12
    if taux_mensuel == 0:
        capacite = mensualite_max * nb_mois
    else:
        capacite = mensualite_max * (1 - (1 + taux_mensuel) ** (-nb_mois)) / taux_mensuel
    return round(capacite / 1000) * 1000
