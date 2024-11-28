from django.db import models

class Device(models.Model):
    generated_id = models.CharField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, null=True, blank=True)
    name_hy = models.CharField(max_length=200, null=True, blank=True)
    parent_name = models.CharField(max_length=200)
    parent_name_en = models.CharField(max_length=200, null=True, blank=True)
    parent_name_hy = models.CharField(max_length=200, null=True, blank=True)
    latitude = models.DecimalField(max_digits=18, decimal_places=15)
    longitude = models.DecimalField(max_digits=18, decimal_places=15)

    def __str__(self):
        return f"{self.generated_id}"

    class Meta:
        db_table = 'backend_device'
