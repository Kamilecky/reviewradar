from django import forms

from .models import UserReview

RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]


class UserReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(choices=RATING_CHOICES, coerce=int)

    class Meta:
        model = UserReview
        fields = ("rating", "comment")
