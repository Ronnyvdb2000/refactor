# Dagelijkse invoer voor scoring

De centrale workflow `refresh_sel_par.yml` zet selecties over en vult daarna
technische kenmerken, fundamentele waarnemingen en vervolgkoersen rechtstreeks
in `sel_par` aan. De oorspronkelijke drie featurebuilder-crons zijn uitgeschakeld.

Alleen na een afgeronde verrijking en voldoende correct gedateerde technische
dekking ontstaat `feature-build-manifest`. De bestaande manifestcontroles blijven
actief: bronrun, poging, commit, selectiegrens, maximale leeftijd en dekking.
Nieuwe modelinvoer wordt daarna vrijgegeven via `invoer_vrijgegeven` in `sel_par`.

XGBoost-scoring en Ridge-meta-scoring volgen uitsluitend geslaagde centrale runs
uit main van dezelfde repository. Ze checken de broncommit uit en downloaden het
manifest van exact die run. Hun eigen invoercontroles blijven actief; een manifest
maakt ontbrekende of onbruikbare kenmerken niet geldig.

Alle modelinvoer komt via `common.sel_par_inputs` uit `sel_par`. De oorspronkelijke
kolomnamen blijven beschikbaar als queryprojecties; er worden geen kopieën van de
oude tabellen gemaakt en er is geen terugval naar die tabellen. Lees
[sel_par.md](sel_par.md) voor herkomst, historische beschikbaarheid, strategieën,
herstarts en de grenzen van de koersopvolging.
