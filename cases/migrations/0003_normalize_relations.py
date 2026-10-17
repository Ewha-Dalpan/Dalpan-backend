from django.db import migrations


def normalize(apps, schema_editor):
    aliases = ["\ud559\uad50\u00b7\ud300\ud50c", "\ud559\uad50,\ud300\ud50c", "\ud559\uad50, \ud300\ud50c"]
    canonical = "\uc9c1\uc7a5\u00b7\ud559\uad50"
    for app, model in [("cases", "CaseSituation"), ("verdicts", "Verdict")]:
        apps.get_model(app, model).objects.using(schema_editor.connection.alias).filter(relation__in=aliases).update(relation=canonical)


class Migration(migrations.Migration):
    dependencies = [("cases", "0002_caseimage_public_image"), ("verdicts", "0007_verdict_public_one_line_verdictfactor_public_name_and_more")]
    operations = [migrations.RunPython(normalize, migrations.RunPython.noop)]
